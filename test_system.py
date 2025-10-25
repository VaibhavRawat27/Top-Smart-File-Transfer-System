#!/usr/bin/env python3
"""
Smart File Transfer System - Test Suite
Validates the complete system functionality
"""

import os
import sys
import time
import requests
import subprocess
import threading
import tempfile
import hashlib
from pathlib import Path

def create_test_file(size_mb=1):
    """Create a test file of specified size"""
    test_file = Path("test_file.bin")
    
    print(f"📝 Creating {size_mb}MB test file...")
    with open(test_file, 'wb') as f:
        # Write random data
        for _ in range(size_mb * 1024):
            f.write(os.urandom(1024))
    
    return test_file

def start_coordinator():
    """Start the coordinator server in background"""
    print("🚀 Starting coordinator server...")
    
    process = subprocess.Popen(
        [sys.executable, "coordinator/app.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=Path.cwd()
    )
    
    # Wait for server to start
    for _ in range(30):  # 30 second timeout
        try:
            response = requests.get("http://localhost:5000/health", timeout=2)
            if response.status_code == 200:
                print("✅ Coordinator server started successfully")
                return process
        except:
            time.sleep(1)
    
    print("❌ Failed to start coordinator server")
    process.terminate()
    return None

def test_file_transfer(test_file):
    """Test file transfer functionality"""
    print(f"📤 Testing file transfer: {test_file}")
    
    # Calculate original file hash
    with open(test_file, 'rb') as f:
        original_hash = hashlib.sha256(f.read()).hexdigest()
    
    print(f"📋 Original file hash: {original_hash}")
    
    # Send file
    result = subprocess.run([
        sys.executable, "sender/send_file.py", str(test_file), "--verbose"
    ], capture_output=True, text=True, cwd=Path.cwd())
    
    if result.returncode != 0:
        print(f"❌ File transfer failed: {result.stderr}")
        return False
    
    print("✅ File transfer completed")
    
    # List files
    result = subprocess.run([
        sys.executable, "sender/receive_file.py", "--list"
    ], capture_output=True, text=True, cwd=Path.cwd())
    
    if result.returncode != 0:
        print(f"❌ File listing failed: {result.stderr}")
        return False
    
    print("✅ File listing successful")
    print(result.stdout)
    
    return True

def test_dashboard():
    """Test dashboard accessibility"""
    print("🌐 Testing dashboard...")
    
    try:
        response = requests.get("http://localhost:5000", timeout=10)
        if response.status_code == 200 and "Smart File Transfer System" in response.text:
            print("✅ Dashboard is accessible")
            return True
        else:
            print(f"❌ Dashboard returned status {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Dashboard test failed: {e}")
        return False

def main():
    """Run the complete test suite"""
    print("🧪 Smart File Transfer System - Test Suite")
    print("=" * 50)
    
    # Check if we're in the right directory
    if not Path("coordinator/app.py").exists():
        print("❌ Please run this script from the project root directory")
        sys.exit(1)
    
    coordinator_process = None
    test_file = None
    
    try:
        # Create test file
        test_file = create_test_file(1)  # 1MB test file
        
        # Start coordinator
        coordinator_process = start_coordinator()
        if not coordinator_process:
            sys.exit(1)
        
        # Test dashboard
        if not test_dashboard():
            sys.exit(1)
        
        # Test file transfer
        if not test_file_transfer(test_file):
            sys.exit(1)
        
        print("\n🎉 All tests passed successfully!")
        print("🌐 Dashboard: http://localhost:5000")
        print("📊 Check the dashboard to see transfer statistics")
        
    except KeyboardInterrupt:
        print("\n🛑 Tests interrupted by user")
    
    except Exception as e:
        print(f"\n💥 Test suite failed: {e}")
        sys.exit(1)
    
    finally:
        # Cleanup
        if coordinator_process:
            print("\n🧹 Cleaning up...")
            coordinator_process.terminate()
            coordinator_process.wait(timeout=5)
        
        if test_file and test_file.exists():
            test_file.unlink()
            print("🗑️  Test file cleaned up")

if __name__ == "__main__":
    main()