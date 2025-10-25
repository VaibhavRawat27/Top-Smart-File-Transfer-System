# 🚀 Smart File Transfer System (SFTS)

A resilient, intelligent file transfer system designed for unstable networks and high-pressure environments. SFTS ensures fast, secure, and uninterrupted file movement across challenging network conditions.

## ✨ Key Features

### 🛡️ **Network Resilience**
- **Adaptive chunk sizing** - Automatically adjusts chunk size based on network conditions
- **Intelligent retry logic** - Exponential backoff with network quality monitoring
- **Resume capability** - Seamlessly resumes interrupted transfers
- **Connection pooling** - Efficient HTTP connection reuse

### 🔒 **Data Integrity**
- **SHA-256 checksums** - Per-chunk integrity verification
- **Duplicate detection** - Prevents redundant chunk uploads
- **File assembly verification** - End-to-end integrity checks

### ⚡ **Performance & Monitoring**
- **Real-time dashboard** - Live transfer progress and statistics
- **Priority channels** - High/normal/low priority transfer queues
- **Transfer analytics** - Speed monitoring, success rates, and ETA calculations
- **Network quality adaptation** - Dynamic optimization based on conditions

### 🎯 **Production Ready**
- **Comprehensive logging** - Detailed operation logs and error tracking
- **Database persistence** - SQLite-based state management with cleanup
- **Graceful shutdown** - Proper cleanup and state preservation
- **Health monitoring** - Built-in health check endpoints

## 🏗️ Architecture

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Sender        │    │   Coordinator   │    │   Receiver      │
│   (Edge Agent)  │◄──►│   (Server)      │◄──►│   (Edge Agent)  │
├─────────────────┤    ├─────────────────┤    ├─────────────────┤
│ • File chunking │    │ • Chunk storage │    │ • File download │
│ • Checksum calc │    │ • Progress track│    │ • Integrity ver │
│ • Retry logic   │    │ • Dashboard     │    │ • File listing  │
│ • Network adapt │    │ • API endpoints │    │ • Verification  │
└─────────────────┘    └─────────────────┘    └─────────────────┘
```

## 📦 Components

### 1. **Coordinator Server** (`coordinator/`)
- Flask-based server with WebSocket support
- SQLite database for state management
- Real-time dashboard with modern UI
- REST API for file operations
- Comprehensive logging and monitoring

### 2. **Sender Client** (`sender/`)
- Intelligent file chunking and upload
- Adaptive network quality monitoring
- Resume capability with progress tracking
- Comprehensive error handling and retry logic

### 3. **Receiver Client** (`sender/receive_file.py`)
- File listing and download capabilities
- Integrity verification tools
- Progress monitoring for downloads

## 🚀 Quick Start

### Prerequisites
- Python 3.8+ 
- Windows/Linux/macOS

### Option 1: Automated Setup (Windows)
```bash
# Run the setup script
setup.bat
```

### Option 2: Manual Setup
```bash
# 1. Create virtual environment
python -m venv venv

# 2. Activate virtual environment
# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

# 3. Install dependencies
pip install -r coordinator/requirements.txt
pip install -r sender/requirements.txt
```

### Running the System

1. **Start the Coordinator Server:**
   ```bash
   python coordinator/app.py
   ```
   Server starts at: http://localhost:5000

2. **Open the Dashboard:**
   Navigate to http://localhost:5000 in your browser

3. **Send a File:**
   ```bash
   # Basic transfer
   python sender/send_file.py demo_files/sample.bin
   
   # High priority transfer with custom chunk size
   python sender/send_file.py myfile.pdf --priority high --chunk-size 512000
   
   # Verbose logging
   python sender/send_file.py largefile.zip --verbose
   ```

4. **List Available Files:**
   ```bash
   python sender/receive_file.py --list
   ```

5. **Download a File:**
   ```bash
   python sender/receive_file.py --download <file-id> --output downloaded_file.pdf
   ```

## 🎛️ Advanced Usage

### Environment Variables
```bash
# Server configuration
export SFTS_SERVER="http://192.168.1.100:5000"
export SFTS_MAX_RETRIES="15"
export SFTS_CHUNK_SIZE="1048576"  # 1MB
export SFTS_TIMEOUT="60"

# Security (production)
export SECRET_KEY="your-secure-secret-key"
```

### Sender Options
```bash
python sender/send_file.py [file] [options]

Options:
  --chunk-size SIZE     Initial chunk size in bytes (default: 256KB)
  --priority LEVEL      Transfer priority: high/normal/low (default: normal)
  --adaptive           Enable adaptive chunk sizing (default: enabled)
  --max-retries NUM    Maximum retries per chunk (default: 10)
  --server URL         Server URL (default: http://127.0.0.1:5000)
  --verbose, -v        Enable verbose logging
```

### Receiver Options
```bash
python sender/receive_file.py [options]

Options:
  --list               List available files
  --download ID        Download file by ID
  --output PATH        Output file path
  --verify PATH        Verify file integrity
  --checksum HASH      Expected checksum for verification
  --server URL         Server URL
  --verbose, -v        Enable verbose logging
```

## 📊 Dashboard Features

The web dashboard provides:
- **Real-time transfer monitoring** with progress bars
- **Network status indicators** (connected/disconnected)
- **Transfer statistics** (speed, ETA, success rate)
- **File management** with priority indicators
- **Error tracking** and detailed logs
- **System health monitoring**

## 🔧 API Endpoints

### File Operations
- `GET /api/files` - List all files
- `GET /api/files/{id}` - Get file information
- `GET /download/{id}` - Download assembled file
- `GET /health` - Health check

### Transfer Operations
- `POST /upload/init` - Initialize transfer
- `POST /upload/chunk` - Upload chunk
- `GET /upload/missing/{id}` - Get missing chunks
- `POST /assemble/{id}` - Assemble file

## 🛠️ Development

### Project Structure
```
ps2/
├── coordinator/           # Server component
│   ├── app.py            # Main server application
│   ├── requirements.txt  # Python dependencies
│   ├── static/           # Web dashboard
│   │   └── index.html    # Dashboard UI
│   ├── uploads/          # File storage (created at runtime)
│   └── logs/             # Server logs (created at runtime)
├── sender/               # Client components
│   ├── send_file.py      # File sender
│   ├── receive_file.py   # File receiver
│   └── requirements.txt  # Python dependencies
├── demo_files/           # Test files
│   └── sample.bin        # Sample test file
├── setup.bat            # Windows setup script
└── README.md            # This file
```

### Testing
```bash
# Test with sample file
python sender/send_file.py demo_files/sample.bin --verbose

# Test large file transfer
python sender/send_file.py large_test_file.zip --priority high

# Test resume capability (interrupt and restart)
python sender/send_file.py test_file.pdf
# Press Ctrl+C to interrupt, then run again to resume
```

## 🌟 Use Cases

### Perfect for:
- **Remote research labs** with unstable internet
- **Media studios** transferring large video files
- **Mobile clinics** in areas with poor connectivity
- **Disaster recovery sites** with limited bandwidth
- **IoT deployments** with intermittent connections
- **Satellite communications** with high latency

### Key Benefits:
- ✅ **Never lose progress** - Resume from exactly where you left off
- ✅ **Adapt to conditions** - Automatically optimizes for your network
- ✅ **Verify integrity** - Cryptographic checksums ensure data accuracy
- ✅ **Monitor everything** - Real-time visibility into transfer status
- ✅ **Handle failures gracefully** - Intelligent retry with exponential backoff

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 🆘 Support

For issues, questions, or contributions:
1. Check the logs in `coordinator/logs/` and `sfts_sender.log`
2. Review the dashboard for transfer status
3. Use `--verbose` flag for detailed debugging
4. Check network connectivity and server accessibility

---

**Built for resilience. Designed for reliability. Optimized for real-world conditions.**
