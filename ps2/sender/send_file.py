#!/usr/bin/env python3
import requests, os, sys, hashlib, uuid, json, time, argparse, logging
from datetime import datetime, timedelta
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import threading
from pathlib import Path

# Configuration
SERVER = os.environ.get('SFTS_SERVER', 'http://127.0.0.1:5000')
MAX_RETRIES = int(os.environ.get('SFTS_MAX_RETRIES', '10'))
INITIAL_CHUNK_SIZE = int(os.environ.get('SFTS_CHUNK_SIZE', str(256 * 1024)))  # 256KB default
MIN_CHUNK_SIZE = 64 * 1024   # 64KB minimum
MAX_CHUNK_SIZE = 10 * 1024 * 1024  # 10MB maximum
TIMEOUT = int(os.environ.get('SFTS_TIMEOUT', '30'))

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('sfts_sender.log')
    ]
)
logger = logging.getLogger(__name__)

# Network resilience setup
def create_resilient_session():
    """Create a requests session with retry strategy and timeouts"""
    session = requests.Session()
    
    retry_strategy = Retry(
        total=3,
        status_forcelist=[429, 500, 502, 503, 504],
        method_whitelist=["HEAD", "GET", "OPTIONS", "POST"],
        backoff_factor=1
    )
    
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    
    return session

# Global session for connection reuse
session = create_resilient_session()

def sha256_bytes(b):
    h = hashlib.sha256()
    h.update(b)
    return h.hexdigest()

def split_file(path, chunk_size):
    size = os.path.getsize(path)
    chunks = []
    with open(path,'rb') as fh:
        idx = 0
        while True:
            data = fh.read(chunk_size)
            if not data:
                break
            checksum = sha256_bytes(data)
            chunks.append({'chunk_id': idx, 'size': len(data), 'checksum': checksum})
            idx += 1
    return chunks

def send_manifest(file_id, filename, size, chunk_size, chunks, priority):
    payload = {
        'file_id': file_id,
        'filename': os.path.basename(filename),
        'size': size,
        'chunk_size': chunk_size,
        'chunks': chunks,
        'priority': priority
    }
    r = requests.post(SERVER + '/upload/init', json=payload)
    r.raise_for_status()
    return r.json()

class NetworkMonitor:
    """Monitor network quality and adapt transfer parameters"""
    def __init__(self):
        self.success_count = 0
        self.failure_count = 0
        self.recent_speeds = []
        self.last_speed_check = time.time()
        
    def record_success(self, bytes_sent, duration):
        self.success_count += 1
        speed = bytes_sent / duration if duration > 0 else 0
        self.recent_speeds.append(speed)
        if len(self.recent_speeds) > 10:
            self.recent_speeds.pop(0)
    
    def record_failure(self):
        self.failure_count += 1
    
    def get_avg_speed(self):
        return sum(self.recent_speeds) / len(self.recent_speeds) if self.recent_speeds else 0
    
    def get_success_rate(self):
        total = self.success_count + self.failure_count
        return self.success_count / total if total > 0 else 1.0
    
    def should_reduce_chunk_size(self):
        return self.get_success_rate() < 0.8 and self.failure_count > 3

network_monitor = NetworkMonitor()

def adaptive_chunk_size(current_size, success_rate, avg_speed):
    """Dynamically adjust chunk size based on network conditions"""
    if success_rate > 0.95 and avg_speed > 1024 * 1024:  # > 1MB/s
        # Network is good, try larger chunks
        new_size = min(current_size * 1.2, MAX_CHUNK_SIZE)
    elif success_rate < 0.8 or avg_speed < 100 * 1024:  # < 100KB/s
        # Network is poor, use smaller chunks
        new_size = max(current_size * 0.7, MIN_CHUNK_SIZE)
    else:
        new_size = current_size
    
    return int(new_size)

def upload_chunk(file_id, chunk_id, data, checksum, max_retries=None):
    """Upload a chunk with adaptive retry logic and network monitoring"""
    if max_retries is None:
        max_retries = MAX_RETRIES
    
    chunk_size = len(data)
    
    for attempt in range(1, max_retries + 1):
        start_time = time.time()
        
        try:
            files = {'chunk': ('chunk', data)}
            form_data = {
                'file_id': file_id,
                'chunk_id': str(chunk_id),
                'checksum': checksum
            }
            
            logger.debug(f"Uploading chunk {chunk_id}, attempt {attempt}/{max_retries} ({chunk_size} bytes)")
            
            response = session.post(
                f"{SERVER}/upload/chunk",
                data=form_data,
                files=files,
                timeout=TIMEOUT
            )
            
            duration = time.time() - start_time
            
            if response.status_code == 200:
                network_monitor.record_success(chunk_size, duration)
                
                result = response.json()
                speed = chunk_size / duration if duration > 0 else 0
                
                logger.info(f"✅ Chunk {chunk_id} uploaded successfully "
                           f"({chunk_size} bytes in {duration:.2f}s, {speed:.0f} B/s) "
                           f"- Progress: {result.get('received', 0)}/{result.get('total', 0)}")
                
                return True, result
            
            else:
                network_monitor.record_failure()
                error_msg = f"Server rejected chunk {chunk_id} (HTTP {response.status_code})"
                
                try:
                    error_detail = response.json().get('error', response.text)
                    error_msg += f": {error_detail}"
                except:
                    error_msg += f": {response.text[:100]}"
                
                logger.warning(error_msg)
                
                # Don't retry on certain errors
                if response.status_code in [400, 404, 409]:
                    logger.error(f"Permanent error for chunk {chunk_id}, not retrying")
                    return False, {'error': error_msg}
        
        except requests.exceptions.Timeout:
            network_monitor.record_failure()
            logger.warning(f"Timeout uploading chunk {chunk_id}, attempt {attempt}/{max_retries}")
        
        except requests.exceptions.ConnectionError as e:
            network_monitor.record_failure()
            logger.warning(f"Connection error uploading chunk {chunk_id}, attempt {attempt}/{max_retries}: {e}")
        
        except Exception as e:
            network_monitor.record_failure()
            logger.error(f"Unexpected error uploading chunk {chunk_id}, attempt {attempt}/{max_retries}: {e}")
        
        # Adaptive backoff based on network conditions
        if attempt < max_retries:
            success_rate = network_monitor.get_success_rate()
            if success_rate < 0.5:
                # Poor network, longer backoff
                backoff = min(2 ** attempt, 30)
            else:
                # Good network, shorter backoff
                backoff = min(0.5 * attempt, 5)
            
            logger.info(f"Retrying chunk {chunk_id} in {backoff:.1f}s...")
            time.sleep(backoff)
    
    logger.error(f"❌ Failed to upload chunk {chunk_id} after {max_retries} attempts")
    return False, {'error': f'Failed after {max_retries} attempts'}

def get_missing(file_id):
    r = requests.get(SERVER + f'/upload/missing/{file_id}')
    r.raise_for_status()
    return r.json().get('missing', [])

def assemble(file_id):
    r = requests.post(SERVER + f'/assemble/{file_id}')
    return r.json()

def print_progress(current, total, start_time, bytes_transferred):
    """Print a nice progress bar with statistics"""
    if total == 0:
        return
    
    progress = current / total
    elapsed = time.time() - start_time
    
    # Calculate speed and ETA
    if elapsed > 0:
        speed = bytes_transferred / elapsed
        eta = (total - current) * (elapsed / current) if current > 0 else 0
    else:
        speed = 0
        eta = 0
    
    # Progress bar
    bar_length = 40
    filled_length = int(bar_length * progress)
    bar = '█' * filled_length + '░' * (bar_length - filled_length)
    
    # Format speed
    if speed > 1024 * 1024:
        speed_str = f"{speed / (1024 * 1024):.1f} MB/s"
    elif speed > 1024:
        speed_str = f"{speed / 1024:.1f} KB/s"
    else:
        speed_str = f"{speed:.0f} B/s"
    
    # Format ETA
    if eta > 3600:
        eta_str = f"{eta / 3600:.1f}h"
    elif eta > 60:
        eta_str = f"{eta / 60:.1f}m"
    else:
        eta_str = f"{eta:.0f}s"
    
    print(f"\r[{bar}] {progress:.1%} ({current}/{total}) | {speed_str} | ETA: {eta_str}", end='', flush=True)

def main():
    """Enhanced main function with comprehensive error handling and adaptive features"""
    ap = argparse.ArgumentParser(description='Smart File Transfer System - Sender')
    ap.add_argument('file', help='File to send')
    ap.add_argument('--chunk-size', type=int, default=INITIAL_CHUNK_SIZE, 
                   help=f'Initial chunk size in bytes (default: {INITIAL_CHUNK_SIZE})')
    ap.add_argument('--priority', choices=['high', 'normal', 'low'], default='normal',
                   help='Transfer priority (default: normal)')
    ap.add_argument('--adaptive', action='store_true', default=True,
                   help='Enable adaptive chunk sizing (default: enabled)')
    ap.add_argument('--max-retries', type=int, default=MAX_RETRIES,
                   help=f'Maximum retries per chunk (default: {MAX_RETRIES})')
    ap.add_argument('--server', default=SERVER,
                   help=f'Server URL (default: {SERVER})')
    ap.add_argument('--verbose', '-v', action='store_true',
                   help='Enable verbose logging')
    
    args = ap.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Validate file
    path = Path(args.file)
    if not path.exists():
        logger.error(f"File not found: {path}")
        sys.exit(1)
    
    if not path.is_file():
        logger.error(f"Path is not a file: {path}")
        sys.exit(1)
    
    size = path.stat().st_size
    if size == 0:
        logger.error("Cannot send empty file")
        sys.exit(1)
    
    # Update global server URL
    global SERVER
    SERVER = args.server
    
    logger.info(f"🚀 Starting transfer: {path.name} ({size:,} bytes)")
    logger.info(f"📡 Server: {SERVER}")
    logger.info(f"⚡ Priority: {args.priority}")
    logger.info(f"📦 Initial chunk size: {args.chunk_size:,} bytes")
    
    file_id = str(uuid.uuid4())
    current_chunk_size = args.chunk_size
    start_time = time.time()
    total_bytes_transferred = 0
    
    try:
        # Initial file splitting
        chunks_meta = split_file(str(path), current_chunk_size)
        logger.info(f"📋 File split into {len(chunks_meta)} chunks")
        
        # Send manifest
        logger.info("📤 Sending manifest to server...")
        manifest_result = send_manifest(file_id, str(path), size, current_chunk_size, chunks_meta, args.priority)
        
        if manifest_result.get('status') == 'resumed':
            logger.info(f"🔄 Resuming transfer: {manifest_result.get('received_chunks', 0)} chunks already received")
        
        # Main upload loop with adaptive chunk sizing
        retry_count = 0
        max_consecutive_failures = 5
        
        while True:
            missing = get_missing(file_id)
            if not missing:
                break
            
            logger.info(f"📊 Missing chunks: {len(missing)}")
            
            # Adaptive chunk sizing based on network conditions
            if args.adaptive and len(missing) > 1:
                success_rate = network_monitor.get_success_rate()
                avg_speed = network_monitor.get_avg_speed()
                new_chunk_size = adaptive_chunk_size(current_chunk_size, success_rate, avg_speed)
                
                if new_chunk_size != current_chunk_size:
                    logger.info(f"🔧 Adapting chunk size: {current_chunk_size:,} → {new_chunk_size:,} bytes "
                               f"(success rate: {success_rate:.1%}, avg speed: {avg_speed:.0f} B/s)")
                    current_chunk_size = new_chunk_size
                    
                    # Re-split file with new chunk size if significantly different
                    if abs(new_chunk_size - args.chunk_size) > args.chunk_size * 0.5:
                        chunks_meta = split_file(str(path), current_chunk_size)
                        send_manifest(file_id, str(path), size, current_chunk_size, chunks_meta, args.priority)
                        continue
            
            consecutive_failures = 0
            
            with open(path, 'rb') as fh:
                for i, chunk_id in enumerate(missing):
                    # Calculate chunk position and size for current chunk size
                    chunk_start = chunk_id * current_chunk_size
                    fh.seek(chunk_start)
                    data = fh.read(current_chunk_size)
                    
                    if not data:
                        logger.warning(f"No data for chunk {chunk_id}, skipping")
                        continue
                    
                    checksum = hashlib.sha256(data).hexdigest()
                    
                    # Progress display
                    completed_chunks = len(chunks_meta) - len(missing) + i
                    print_progress(completed_chunks, len(chunks_meta), start_time, total_bytes_transferred)
                    
                    # Upload chunk
                    success, result = upload_chunk(file_id, chunk_id, data, checksum, args.max_retries)
                    
                    if success:
                        total_bytes_transferred += len(data)
                        consecutive_failures = 0
                        retry_count = 0
                    else:
                        consecutive_failures += 1
                        retry_count += 1
                        
                        if consecutive_failures >= max_consecutive_failures:
                            logger.error(f"❌ Too many consecutive failures ({consecutive_failures}), aborting")
                            sys.exit(2)
                        
                        if retry_count > args.max_retries * 2:
                            logger.error(f"❌ Too many total retries ({retry_count}), aborting")
                            sys.exit(2)
                        
                        logger.warning(f"⚠️  Chunk {chunk_id} failed, will retry in next iteration")
                        break  # Break inner loop to refresh missing chunks list
            
            # Brief pause between iterations to avoid overwhelming the server
            time.sleep(0.1)
        
        print()  # New line after progress bar
        logger.info("✅ All chunks uploaded successfully!")
        
        # Request assembly
        logger.info("🔧 Requesting file assembly...")
        assembly_result = assemble(file_id)
        
        if assembly_result.get('status') == 'ok':
            elapsed_total = time.time() - start_time
            avg_speed = size / elapsed_total if elapsed_total > 0 else 0
            
            logger.info("🎉 Transfer completed successfully!")
            logger.info(f"📊 Statistics:")
            logger.info(f"   • Total time: {elapsed_total:.1f}s")
            logger.info(f"   • Average speed: {avg_speed / 1024:.1f} KB/s")
            logger.info(f"   • Success rate: {network_monitor.get_success_rate():.1%}")
            logger.info(f"   • Total retries: {retry_count}")
            logger.info(f"   • Final chunk size: {current_chunk_size:,} bytes")
            
            if 'path' in assembly_result:
                logger.info(f"   • Server path: {assembly_result['path']}")
        else:
            logger.error(f"❌ Assembly failed: {assembly_result}")
            sys.exit(3)
    
    except KeyboardInterrupt:
        print()  # New line after progress bar
        logger.info("🛑 Transfer interrupted by user")
        logger.info("💡 You can resume this transfer later using the same file")
        sys.exit(130)
    
    except Exception as e:
        print()  # New line after progress bar
        logger.error(f"💥 Unexpected error: {e}")
        logger.debug("Full traceback:", exc_info=True)
        sys.exit(1)

if __name__ == '__main__':
    main()
