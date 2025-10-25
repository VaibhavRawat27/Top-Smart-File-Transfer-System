from flask import Flask, request, jsonify, send_from_directory, render_template_string
from flask_socketio import SocketIO, emit
import os, sqlite3, hashlib, json, pathlib, logging, time, threading
from datetime import datetime, timedelta
from collections import defaultdict
import signal
import sys
import io

# Configuration
BASE_DIR = pathlib.Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
DB_PATH = BASE_DIR / "state.db"
LOG_DIR = BASE_DIR / "logs"
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

# Logging setup
stdout_stream = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / 'coordinator.log', encoding='utf-8'),
        logging.StreamHandler(stdout_stream)
    ]
)
logger = logging.getLogger(__name__)

# Flask app setup
app = Flask(__name__, static_folder=str(BASE_DIR / 'static'))
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-key-change-in-production')
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB max chunk size
socketio = SocketIO(app, cors_allowed_origins='*', logger=False, engineio_logger=False)

# Global state for monitoring
transfer_stats = defaultdict(lambda: {
    'start_time': None,
    'last_activity': None,
    'bytes_received': 0,
    'chunks_received': 0,
    'errors': 0
})

# Simple SQLite state: manifests table
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS manifests
                 (file_id TEXT PRIMARY KEY, filename TEXT, size INTEGER, chunk_size INTEGER, total_chunks INTEGER, merkle TEXT, priority TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS chunks
                 (file_id TEXT, chunk_id INTEGER, checksum TEXT, received INTEGER, PRIMARY KEY(file_id, chunk_id))''')
    conn.commit()
    conn.close()

init_db()

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/static/<path:p>')
def static_files(p):
    return send_from_directory(str(BASE_DIR / 'static'), p)

@app.route('/upload/init', methods=['POST'])
def upload_init():
    data = request.get_json()
    file_id = data['file_id']
    filename = data['filename']
    size = data['size']
    chunk_size = data['chunk_size']
    chunks = data['chunks']  # list of {chunk_id, checksum, size}
    priority = data.get('priority', 'normal')
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''INSERT OR REPLACE INTO manifests
                 (file_id, filename, size, chunk_size, total_chunks, merkle, priority, status)
                 VALUES (?,?,?,?,?,?,?,?)''',
              (file_id, filename, size, chunk_size, len(chunks), '', priority, 'active'))
    for ch in chunks:
        c.execute('INSERT OR REPLACE INTO chunks (file_id, chunk_id, checksum, received) VALUES (?,?,?,?)',
                  (file_id, ch['chunk_id'], ch['checksum'], 0))
    conn.commit()
    conn.close()
    socketio.emit('manifest', {'file_id': file_id, 'filename': filename, 'size': size, 'total_chunks': len(chunks), 'priority': priority})
    return jsonify({'status':'ok'})

@app.route('/upload/chunk', methods=['POST'])
def upload_chunk():
    """Upload a file chunk with enhanced validation and monitoring"""
    start_time = time.time()
    
    try:
        # Validate required form fields
        required_fields = ['file_id', 'chunk_id', 'checksum']
        for field in required_fields:
            if field not in request.form:
                return jsonify({'error': f'Missing required field: {field}'}), 400
        
        file_id = request.form['file_id']
        try:
            chunk_id = int(request.form['chunk_id'])
        except ValueError:
            return jsonify({'error': 'Invalid chunk_id format'}), 400
        
        checksum = request.form['checksum']
        
        # Validate chunk file
        if 'chunk' not in request.files:
            return jsonify({'error': 'No chunk file provided'}), 400
        
        f = request.files['chunk']
        if f.filename == '':
            return jsonify({'error': 'Empty chunk file'}), 400
        
        # Read and validate chunk data
        data = f.read()
        if len(data) == 0:
            return jsonify({'error': 'Empty chunk data'}), 400
        
        # Verify checksum
        m = hashlib.sha256()
        m.update(data)
        calculated_checksum = m.hexdigest()
        
        if calculated_checksum != checksum:
            logger.warning(f"Checksum mismatch for {file_id} chunk {chunk_id}: expected {checksum}, got {calculated_checksum}")
            
            # Update error statistics
            transfer_stats[file_id]['errors'] += 1
            
            # Emit error to dashboard
            socketio.emit('error', {
                'file_id': file_id,
                'chunk_id': chunk_id,
                'message': f'Checksum mismatch for chunk {chunk_id}'
            })
            
            return jsonify({
                'error': 'Checksum verification failed',
                'expected': checksum,
                'received': calculated_checksum
            }), 400
        
        conn = get_db_connection()
        c = conn.cursor()
        
        # Verify file_id exists and is active
        c.execute('SELECT status, filename, total_chunks FROM manifests WHERE file_id = ?', (file_id,))
        manifest = c.fetchone()
        
        if not manifest:
            return jsonify({'error': 'Unknown file_id'}), 404
        
        if manifest['status'] != 'active':
            return jsonify({'error': f'Transfer not active (status: {manifest["status"]})'}), 409
        
        # Check if chunk already received (idempotency)
        c.execute('SELECT received FROM chunks WHERE file_id = ? AND chunk_id = ?', (file_id, chunk_id))
        chunk_status = c.fetchone()
        
        if not chunk_status:
            return jsonify({'error': 'Invalid chunk_id for this file'}), 400
        
        if chunk_status['received'] == 1:
            logger.info(f"Chunk {chunk_id} for {file_id} already received (duplicate)")
            
            # Still return success for idempotency, but don't reprocess
            c.execute('SELECT COUNT(*) as received FROM chunks WHERE file_id = ? AND received = 1', (file_id,))
            received_count = c.fetchone()['received']
            
            return jsonify({
                'status': 'ok',
                'received': received_count,
                'total': manifest['total_chunks'],
                'duplicate': True
            })
        
        # Write chunk to disk
        ch_dir = UPLOAD_DIR / file_id
        ch_dir.mkdir(parents=True, exist_ok=True)
        chunk_path = ch_dir / f'chunk_{chunk_id:06d}.bin'
        
        try:
            with open(chunk_path, 'wb') as fh:
                fh.write(data)
        except IOError as e:
            logger.error(f"Failed to write chunk {chunk_id} for {file_id}: {e}")
            return jsonify({'error': 'Failed to write chunk to disk'}), 500
        
        # Mark chunk as received
        c.execute('''UPDATE chunks 
                     SET received = 1, received_at = ?, retry_count = retry_count + 1 
                     WHERE file_id = ? AND chunk_id = ?''',
                  (datetime.now(), file_id, chunk_id))
        
        # Update transfer statistics
        transfer_stats[file_id]['last_activity'] = datetime.now()
        transfer_stats[file_id]['bytes_received'] += len(data)
        transfer_stats[file_id]['chunks_received'] += 1
        
        # Get current progress
        c.execute('SELECT COUNT(*) as received FROM chunks WHERE file_id = ? AND received = 1', (file_id,))
        received_count = c.fetchone()['received']
        
        # Update database statistics
        elapsed_time = time.time() - transfer_stats[file_id]['start_time'].timestamp()
        avg_speed = transfer_stats[file_id]['bytes_received'] / elapsed_time if elapsed_time > 0 else 0
        
        c.execute('''UPDATE transfer_stats 
                     SET chunks_received = ?, avg_speed = ?, errors = ?
                     WHERE file_id = ?''',
                  (received_count, avg_speed, transfer_stats[file_id]['errors'], file_id))
        
        conn.commit()
        
        # Calculate transfer speed for this chunk
        chunk_time = time.time() - start_time
        chunk_speed = len(data) / chunk_time if chunk_time > 0 else 0
        
        logger.info(f"Received chunk {chunk_id}/{manifest['total_chunks']} for {file_id} "
                   f"({received_count}/{manifest['total_chunks']} total, {len(data)} bytes, "
                   f"{chunk_speed:.2f} B/s)")
        
        # Emit progress to dashboard
        socketio.emit('chunk', {
            'file_id': file_id,
            'chunk_id': chunk_id,
            'received': received_count,
            'total': manifest['total_chunks'],
            'filename': manifest['filename'],
            'chunk_size': len(data),
            'speed': chunk_speed
        })
        
        # Check if transfer is complete
        if received_count == manifest['total_chunks']:
            logger.info(f"All chunks received for {file_id}, ready for assembly")
            socketio.emit('transfer_complete', {
                'file_id': file_id,
                'filename': manifest['filename']
            })
        
        return jsonify({
            'status': 'ok',
            'received': received_count,
            'total': manifest['total_chunks'],
            'speed': chunk_speed,
            'progress': round((received_count / manifest['total_chunks']) * 100, 2)
        })
        
    except sqlite3.Error as e:
        logger.error(f"Database error in upload_chunk: {e}")
        return jsonify({'error': 'Database error'}), 500
    
    except Exception as e:
        logger.error(f"Unexpected error in upload_chunk: {e}")
        return jsonify({'error': 'Internal server error'}), 500
    
    finally:
        if 'conn' in locals():
            conn.close()

@app.route('/upload/missing/<file_id>', methods=['GET'])
def missing(file_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT chunk_id FROM chunks WHERE file_id=? AND received=0', (file_id,))
    rows = c.fetchall()
    conn.close()
    missing = [r[0] for r in rows]
    return jsonify({'missing': missing})

@app.route('/assemble/<file_id>', methods=['POST'])
def assemble(file_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT filename,total_chunks,chunk_size FROM manifests WHERE file_id=?', (file_id,))
    row = c.fetchone()
    if not row:
        return jsonify({'error':'unknown file id'}), 404
    filename, total, chunk_size = row
    ch_dir = UPLOAD_DIR / file_id
    out_path = UPLOAD_DIR / f'assembled_{filename}'
    with open(out_path, 'wb') as out:
        for i in range(total):
            p = ch_dir / f'chunk_{i:06d}.bin'
            if not p.exists():
                return jsonify({'error':f'missing chunk {i}'}), 400
            with open(p,'rb') as fh:
                out.write(fh.read())
    # verify full file hash if provided
    socketio.emit('assembled', {'file_id':file_id, 'filename': filename})
    return jsonify({'status':'ok','path':str(out_path)})

@app.route('/api/files', methods=['GET'])
def list_files():
    """List all available files"""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        
        c.execute('''SELECT file_id, filename, size, status, created_at, completed_at, priority
                     FROM manifests 
                     ORDER BY created_at DESC''')
        
        files = []
        for row in c.fetchall():
            files.append({
                'file_id': row['file_id'],
                'filename': row['filename'],
                'size': row['size'],
                'status': row['status'],
                'created_at': row['created_at'],
                'completed_at': row['completed_at'],
                'priority': row['priority']
            })
        
        return jsonify(files)
        
    except sqlite3.Error as e:
        logger.error(f"Database error in list_files: {e}")
        return jsonify({'error': 'Database error'}), 500
    
    finally:
        if 'conn' in locals():
            conn.close()

@app.route('/api/files/<file_id>', methods=['GET'])
def get_file_info(file_id):
    """Get information about a specific file"""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        
        c.execute('''SELECT file_id, filename, size, status, created_at, completed_at, priority, total_chunks
                     FROM manifests 
                     WHERE file_id = ?''', (file_id,))
        
        row = c.fetchone()
        if not row:
            return jsonify({'error': 'File not found'}), 404
        
        # Get chunk progress
        c.execute('SELECT COUNT(*) as received FROM chunks WHERE file_id = ? AND received = 1', (file_id,))
        received_chunks = c.fetchone()['received']
        
        file_info = {
            'file_id': row['file_id'],
            'filename': row['filename'],
            'size': row['size'],
            'status': row['status'],
            'created_at': row['created_at'],
            'completed_at': row['completed_at'],
            'priority': row['priority'],
            'total_chunks': row['total_chunks'],
            'received_chunks': received_chunks,
            'progress': (received_chunks / row['total_chunks']) * 100 if row['total_chunks'] > 0 else 0
        }
        
        return jsonify(file_info)
        
    except sqlite3.Error as e:
        logger.error(f"Database error in get_file_info: {e}")
        return jsonify({'error': 'Database error'}), 500
    
    finally:
        if 'conn' in locals():
            conn.close()

@app.route('/download/<file_id>', methods=['GET'])
def download_file(file_id):
    """Download an assembled file"""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        
        c.execute('SELECT filename, status FROM manifests WHERE file_id = ?', (file_id,))
        row = c.fetchone()
        
        if not row:
            return jsonify({'error': 'File not found'}), 404
        
        filename = row['filename']
        status = row['status']
        
        if status != 'completed':
            return jsonify({'error': 'File not ready for download', 'status': status}), 409
        
        # Check if assembled file exists
        assembled_path = UPLOAD_DIR / f'assembled_{filename}'
        
        if not assembled_path.exists():
            logger.warning(f"Assembled file not found: {assembled_path}")
            return jsonify({'error': 'Assembled file not found'}), 404
        
        logger.info(f"Serving download: {filename} to client")
        
        return send_from_directory(
            str(UPLOAD_DIR),
            f'assembled_{filename}',
            as_attachment=True,
            download_name=filename
        )
        
    except sqlite3.Error as e:
        logger.error(f"Database error in download_file: {e}")
        return jsonify({'error': 'Database error'}), 500
    
    except Exception as e:
        logger.error(f"Error serving download: {e}")
        return jsonify({'error': 'Download failed'}), 500
    
    finally:
        if 'conn' in locals():
            conn.close()

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    try:
        # Test database connection
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('SELECT 1')
        conn.close()
        
        return jsonify({
            'status': 'healthy',
            'timestamp': datetime.now().isoformat(),
            'version': '1.0.0'
        })
    
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return jsonify({
            'status': 'unhealthy',
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

# Graceful shutdown handling
def signal_handler(sig, frame):
    logger.info("Received shutdown signal, cleaning up...")
    cleanup_stale_transfers()
    logger.info("Coordinator server shutting down")
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

if __name__ == '__main__':
    logger.info("Starting Smart File Transfer System Coordinator")
    logger.info(f"Upload directory: {UPLOAD_DIR}")
    logger.info(f"Database: {DB_PATH}")
    logger.info("Dashboard: http://0.0.0.0:5000")
    try:
        port = int(os.getenv('PORT', '5000'))
        socketio.run(app, host='0.0.0.0', port=port)
    except Exception as e:
        logger.error(f"Failed to start server: {e}")
        sys.exit(1)


# Enhanced SQLite state management + migration
def get_db_connection():
    """
    Open a SQLite connection with Row factory and timeout.
    """
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn

def _ensure_columns(cursor, table, required_defs):
    """
    Add missing columns to a table without dropping data.
    required_defs: list of column definitions like "status TEXT DEFAULT 'active'"
    """
    cursor.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in cursor.fetchall()}
    for col_def in required_defs:
        name = col_def.split()[0]
        if name not in existing:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")

def init_db():
    """
    Create tables if needed and ensure columns used by routes exist.
    """
    conn = get_db_connection()
    c = conn.cursor()
    # Base tables
    c.execute('''CREATE TABLE IF NOT EXISTS manifests (
        file_id TEXT PRIMARY KEY,
        filename TEXT NOT NULL,
        size INTEGER NOT NULL,
        chunk_size INTEGER NOT NULL,
        total_chunks INTEGER NOT NULL
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS chunks (
        file_id TEXT,
        chunk_id INTEGER,
        checksum TEXT NOT NULL,
        received INTEGER DEFAULT 0,
        PRIMARY KEY(file_id, chunk_id)
    )''')
    # Migrate extra columns used by current code
    _ensure_columns(c, 'manifests', [
        "merkle TEXT",
        "priority TEXT DEFAULT 'normal'",
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        "completed_at TIMESTAMP",
        "status TEXT DEFAULT 'active'"
    ])
    _ensure_columns(c, 'chunks', [
        "received_at TIMESTAMP",
        "retry_count INTEGER DEFAULT 0"
    ])
    # Stats table
    c.execute('''CREATE TABLE IF NOT EXISTS transfer_stats (
        file_id TEXT PRIMARY KEY,
        start_time TIMESTAMP,
        end_time TIMESTAMP,
        total_bytes INTEGER,
        chunks_received INTEGER,
        errors INTEGER DEFAULT 0,
        avg_speed REAL,
        FOREIGN KEY(file_id) REFERENCES manifests(file_id)
    )''')
    # Indexes
    c.execute('CREATE INDEX IF NOT EXISTS idx_chunks_file_received ON chunks(file_id, received)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_manifests_status ON manifests(status)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_manifests_priority ON manifests(priority)')
    conn.commit()
    conn.close()

def cleanup_stale_transfers():
    try:
        conn = get_db_connection()
        c = conn.cursor()
        stale_time = datetime.now() - timedelta(hours=1)
        c.execute('''UPDATE manifests
                     SET status='stale'
                     WHERE status='active' AND created_at < ?''', (stale_time,))
        conn.commit()
    except sqlite3.Error as e:
        logger.error(f"Error cleaning up stale transfers: {e}")
    finally:
        try:
            conn.close()
        except:
            pass

init_db()
threading.Thread(target=lambda: (time.sleep(3600), cleanup_stale_transfers()), daemon=True).start()
