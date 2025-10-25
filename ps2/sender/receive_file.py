#!/usr/bin/env python3
"""
Smart File Transfer System - Receiver
Downloads and verifies files from the coordinator server
"""

import requests
import os
import sys
import hashlib
import argparse
import logging
from pathlib import Path
import json
from datetime import datetime

# Configuration
SERVER = os.environ.get('SFTS_SERVER', 'http://127.0.0.1:5000')
TIMEOUT = int(os.environ.get('SFTS_TIMEOUT', '30'))

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('sfts_receiver.log')
    ]
)
logger = logging.getLogger(__name__)

def list_available_files():
    """List all available files on the server"""
    try:
        response = requests.get(f"{SERVER}/api/files", timeout=TIMEOUT)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error(f"Failed to list files: {e}")
        return None

def download_file(file_id, output_path=None):
    """Download a file from the server"""
    try:
        # Get file info first
        response = requests.get(f"{SERVER}/api/files/{file_id}", timeout=TIMEOUT)
        response.raise_for_status()
        file_info = response.json()
        
        if not file_info:
            logger.error(f"File {file_id} not found")
            return False
        
        filename = file_info['filename']
        size = file_info['size']
        
        if output_path is None:
            output_path = Path(filename)
        else:
            output_path = Path(output_path)
            if output_path.is_dir():
                output_path = output_path / filename
        
        logger.info(f"📥 Downloading {filename} ({size:,} bytes) to {output_path}")
        
        # Download the file
        response = requests.get(f"{SERVER}/download/{file_id}", stream=True, timeout=TIMEOUT)
        response.raise_for_status()
        
        downloaded = 0
        start_time = datetime.now()
        
        with open(output_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    
                    # Progress update every MB
                    if downloaded % (1024 * 1024) == 0:
                        elapsed = (datetime.now() - start_time).total_seconds()
                        speed = downloaded / elapsed if elapsed > 0 else 0
                        progress = (downloaded / size) * 100 if size > 0 else 0
                        
                        print(f"\r📊 Progress: {progress:.1f}% ({downloaded:,}/{size:,} bytes) "
                              f"Speed: {speed/1024:.1f} KB/s", end='', flush=True)
        
        print()  # New line after progress
        
        # Verify file size
        actual_size = output_path.stat().st_size
        if actual_size != size:
            logger.error(f"Size mismatch: expected {size}, got {actual_size}")
            return False
        
        logger.info(f"✅ Download completed: {output_path}")
        return True
        
    except requests.RequestException as e:
        logger.error(f"Download failed: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return False

def verify_file_integrity(file_path, expected_checksum=None):
    """Verify file integrity using SHA-256"""
    try:
        logger.info(f"🔍 Verifying integrity of {file_path}")
        
        sha256_hash = hashlib.sha256()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha256_hash.update(chunk)
        
        calculated_checksum = sha256_hash.hexdigest()
        
        if expected_checksum:
            if calculated_checksum == expected_checksum:
                logger.info("✅ File integrity verified")
                return True
            else:
                logger.error(f"❌ Checksum mismatch: expected {expected_checksum}, got {calculated_checksum}")
                return False
        else:
            logger.info(f"📋 File checksum: {calculated_checksum}")
            return True
            
    except Exception as e:
        logger.error(f"Integrity check failed: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description='Smart File Transfer System - Receiver')
    parser.add_argument('--server', default=SERVER, help=f'Server URL (default: {SERVER})')
    parser.add_argument('--list', action='store_true', help='List available files')
    parser.add_argument('--download', help='File ID to download')
    parser.add_argument('--output', '-o', help='Output file path')
    parser.add_argument('--verify', help='Verify integrity of local file')
    parser.add_argument('--checksum', help='Expected checksum for verification')
    parser.add_argument('--verbose', '-v', action='store_true', help='Enable verbose logging')
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Update global server URL
    global SERVER
    SERVER = args.server
    
    if args.list:
        logger.info(f"📡 Connecting to server: {SERVER}")
        files = list_available_files()
        
        if files is None:
            sys.exit(1)
        
        if not files:
            logger.info("No files available")
            return
        
        print("\n📁 Available files:")
        print("-" * 80)
        print(f"{'File ID':<36} {'Filename':<30} {'Size':<12} {'Status':<10}")
        print("-" * 80)
        
        for file_info in files:
            size_str = f"{file_info['size']:,} B"
            print(f"{file_info['file_id']:<36} {file_info['filename']:<30} {size_str:<12} {file_info['status']:<10}")
        
        print("-" * 80)
        
    elif args.download:
        logger.info(f"📡 Connecting to server: {SERVER}")
        success = download_file(args.download, args.output)
        
        if not success:
            sys.exit(1)
        
    elif args.verify:
        file_path = Path(args.verify)
        
        if not file_path.exists():
            logger.error(f"File not found: {file_path}")
            sys.exit(1)
        
        success = verify_file_integrity(file_path, args.checksum)
        
        if not success:
            sys.exit(1)
    
    else:
        parser.print_help()
        sys.exit(1)

if __name__ == '__main__':
    main()