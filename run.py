#!/usr/bin/env python3
"""
WoW Raid Recorder with Web frontend.
Runs the recorder and web interface in a single process with WebSocket communication.
"""

import sys
import time
import signal
import argparse
import threading
import asyncio
from pathlib import Path

from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit  # Make sure emit is imported!

from config_manager import ConfigManager
from obs_client import OBSClient
from state_manager import RecordingState
from combat_parser.parser import CombatParser
from log_watcher import LogMonitor
from typing import Optional

from constants import (
    DEFAULT_WEB_HOST,
    DEFAULT_WEB_PORT,
    FLASK_SECRET_KEY,
    MAX_EVENT_LOG_SIZE,
    STATUS_BROADCAST_INTERVAL,
    LOG_PREFIXES,
)

from cloud_integration import (
    CloudUploadManager,
    initialize_cloud_upload,
    should_auto_upload,
)
from cloud_upload import UploadProgress

cloud_manager: Optional[CloudUploadManager] = None

# -----------------------------------------------------------------------------
# Flask App Setup
# -----------------------------------------------------------------------------

app = Flask(__name__)
app.config['SECRET_KEY'] = FLASK_SECRET_KEY
socketio = SocketIO(app, cors_allowed_origins="*")

config_manager: ConfigManager = None
obs_client: OBSClient = None
state_manager: RecordingState = None
log_monitor: LogMonitor = None
combat_parser: CombatParser = None
recorder_running = False
shutdown_event = threading.Event()
event_log: list = []


# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------

@app.route('/')
def index():
    """Serve the main dashboard page."""
    return render_template('index.html')


@app.route('/config')
def config_page():
    """Serve the configuration page."""
    return render_template('config.html')


@app.route('/api/status')
def get_status():
    """Get current recorder status."""
    status = build_status()
    return jsonify(status)


@app.route('/api/config', methods=['GET'])
def get_config():
    """Return current configuration as JSON."""
    if config_manager is None:
        return jsonify({'error': 'Config not initialized'}), 500

    config_data = {
        'general': {
            'log_dir': str(config_manager.LOG_DIR),
            'log_pattern': config_manager.config.get('General', 'log_pattern', raw=True),
            'recording_extension': config_manager.RECORDING_EXTENSION,
        },
        'obs': {
            'host': config_manager.OBS_HOST,
            'port': config_manager.OBS_PORT,
            'password': config_manager.OBS_PASSWORD,
        },
        'recording': {
            'auto_rename': config_manager.AUTO_RENAME,
            'rename_delay': config_manager.RENAME_DELAY,
            'max_rename_attempts': config_manager.MAX_RENAME_ATTEMPTS,
            'min_recording_duration': config_manager.MIN_RECORDING_DURATION,
            'delete_short_recordings': config_manager.DELETE_SHORT_RECORDINGS,
            'recording_path_fallback': str(config_manager.RECORDING_PATH_FALLBACK or ''),
            'dungeon_timeout_seconds': config_manager.DUNGEON_TIMEOUT_SECONDS,
            'file_naming_scheme': config_manager.FILE_NAMING_SCHEME,
            'generate_metadata_json': config_manager.GENERATE_METADATA_JSON,
            'track_player_deaths': config_manager.TRACK_PLAYER_DEATHS,
        },
        'difficulties': {
            'record_lfr': config_manager.RECORD_LFR,
            'record_normal': config_manager.RECORD_NORMAL,
            'record_heroic': config_manager.RECORD_HEROIC,
            'record_mythic': config_manager.RECORD_MYTHIC,
            'record_other': config_manager.RECORD_OTHER,
            'record_mplus': config_manager.RECORD_MPLUS,
        },
        'boss_names': config_manager.BOSS_NAME_OVERRIDES,
        'cloud_upload': {
            'enabled': config_manager.CLOUD_UPLOAD_ENABLED,
            'provider': config_manager.CLOUD_UPLOAD_PROVIDER,
            'auto_upload': config_manager.CLOUD_AUTO_UPLOAD,
            'delete_after_upload': config_manager.CLOUD_DELETE_AFTER_UPLOAD,
            'upload_on_startup': config_manager.CLOUD_UPLOAD_ON_STARTUP,
            'wcr_username': config_manager.WCR_USERNAME,
            'wcr_password': config_manager.WCR_PASSWORD,
            'wcr_guild': config_manager.WCR_GUILD,
        },
    }

    return jsonify(config_data)


@app.route('/api/config', methods=['POST'])
def save_config():
    """Save configuration changes."""
    if config_manager is None:
        return jsonify({'error': 'Config not initialized'}), 500

    try:
        data = request.get_json()

        if 'general' in data:
            general = data['general']
            if 'log_dir' in general:
                config_manager.config.set('General', 'log_dir', general['log_dir'])
            if 'log_pattern' in general:
                config_manager.config.set('General', 'log_pattern', general['log_pattern'])
            if 'recording_extension' in general:
                config_manager.config.set('General', 'recording_extension', general['recording_extension'])

        if 'obs' in data:
            obs = data['obs']
            if 'host' in obs:
                config_manager.config.set('OBS', 'host', obs['host'])
            if 'port' in obs:
                config_manager.config.set('OBS', 'port', str(obs['port']))
            if 'password' in obs:
                config_manager.config.set('OBS', 'password', obs['password'])


        if 'recording' in data:
            recording = data['recording']
            if 'auto_rename' in recording:
                config_manager.config.set('Recording', 'auto_rename', str(recording['auto_rename']).lower())
            if 'rename_delay' in recording:
                config_manager.config.set('Recording', 'rename_delay', str(recording['rename_delay']))
            if 'max_rename_attempts' in recording:
                config_manager.config.set('Recording', 'max_rename_attempts', str(recording['max_rename_attempts']))
            if 'min_recording_duration' in recording:
                config_manager.config.set('Recording', 'min_recording_duration', str(recording['min_recording_duration']))
            if 'delete_short_recordings' in recording:
                config_manager.config.set('Recording', 'delete_short_recordings', str(recording['delete_short_recordings']).lower())
            if 'recording_path_fallback' in recording:
                config_manager.config.set('Recording', 'recording_path_fallback', recording['recording_path_fallback'])
            if 'dungeon_timeout_seconds' in recording:  # NEW
                config_manager.config.set('Recording', 'dungeon_timeout_seconds', str(recording['dungeon_timeout_seconds']))
            if 'file_naming_scheme' in recording:
                config_manager.config.set('Recording', 'file_naming_scheme', str(recording['file_naming_scheme']))
            if 'generate_metadata_json' in recording:
                config_manager.config.set('Recording', 'generate_metadata_json', str(recording['generate_metadata_json']).lower())
            if 'track_player_deaths' in recording:
                config_manager.config.set('Recording', 'track_player_deaths', str(recording['track_player_deaths']).lower())

        if 'difficulties' in data:
            difficulties = data['difficulties']
            if 'record_lfr' in difficulties:
                config_manager.config.set('Difficulties', 'record_lfr', str(difficulties['record_lfr']).lower())
            if 'record_normal' in difficulties:
                config_manager.config.set('Difficulties', 'record_normal', str(difficulties['record_normal']).lower())
            if 'record_heroic' in difficulties:
                config_manager.config.set('Difficulties', 'record_heroic', str(difficulties['record_heroic']).lower())
            if 'record_mythic' in difficulties:
                config_manager.config.set('Difficulties', 'record_mythic', str(difficulties['record_mythic']).lower())
            if 'record_other' in difficulties:
                config_manager.config.set('Difficulties', 'record_other', str(difficulties['record_other']).lower())
            if 'record_mplus' in difficulties:  # NEW
                config_manager.config.set('Difficulties', 'record_mplus', str(difficulties['record_mplus']).lower())

        if 'cloud_upload' in data:
            cloud = data['cloud_upload']
            if 'enabled' in cloud:
                config_manager.config.set('CloudUpload', 'enabled', str(cloud['enabled']).lower())
            if 'provider' in cloud:
                config_manager.config.set('CloudUpload', 'provider', cloud['provider'])
            if 'auto_upload' in cloud:
                config_manager.config.set('CloudUpload', 'auto_upload', str(cloud['auto_upload']).lower())
            if 'delete_after_upload' in cloud:
                config_manager.config.set('CloudUpload', 'delete_after_upload', str(cloud['delete_after_upload']).lower())
            if 'upload_on_startup' in cloud:
                config_manager.config.set('CloudUpload', 'upload_on_startup', str(cloud['upload_on_startup']).lower())
            if 'wcr_username' in cloud:
                config_manager.config.set('CloudUpload', 'wcr_username', cloud['wcr_username'])
            if 'wcr_password' in cloud:
                config_manager.config.set('CloudUpload', 'wcr_password', cloud['wcr_password'])
            if 'wcr_guild' in cloud:
                config_manager.config.set('CloudUpload', 'wcr_guild', cloud['wcr_guild'])


        config_manager.save()

        return jsonify({'success': True, 'message': 'Configuration saved'})

    except Exception as e:
        return jsonify({'error': str(e)}), 400


# -----------------------------------------------------------------------------
# Recordings
# -----------------------------------------------------------------------------

@app.route('/recordings')
def recordings_page():
    """Serve the recordings page."""
    return render_template('recordings.html')


def get_recording_directory() -> Path:
    """Get the recordings directory, with fallback to config."""
    # Try combat_parser's file_manager first (which checks OBS)
    if combat_parser and combat_parser.file_manager:
        try:
            record_dir = combat_parser.file_manager.get_recording_directory()
            if record_dir and record_dir.exists():
                return record_dir
        except Exception as e:
            print(f"[RECORDINGS] Error getting directory from file_manager: {e}")

    # Fallback to config
    if config_manager and config_manager.RECORDING_PATH_FALLBACK:
        fallback = config_manager.RECORDING_PATH_FALLBACK
        if fallback.exists():
            return fallback

    return None


def list_recording_files() -> list:
    """List all recording files in the recordings directory."""
    record_dir = get_recording_directory()
    if not record_dir or not record_dir.exists():
        return []

    ext = config_manager.RECORDING_EXTENSION.lower() if config_manager else '.mp4'
    recordings = []
    for file in record_dir.iterdir():
        if file.suffix.lower() == ext and file.is_file():
            stat = file.stat()
            recordings.append({
                'name': file.name,
                'size': stat.st_size,
                'modified': stat.st_mtime,
            })

    recordings.sort(key=lambda x: x['modified'], reverse=True)
    return recordings


@app.route('/api/recordings')
def get_recordings():
    """Get list of recordings."""
    try:
        recordings = list_recording_files()
        record_dir = get_recording_directory()
        return jsonify({
            'recordings': recordings,
            'directory': str(record_dir) if record_dir else None,
        })
    except Exception as e:
        print(f"[RECORDINGS] Error in get_recordings: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/recordings/<path:filename>', methods=['DELETE'])
def delete_recording_endpoint(filename: str):
    """Delete a recording file."""
    try:
        record_dir = get_recording_directory()
        if not record_dir:
            return jsonify({'error': 'Recording directory not available'}), 500

        file_path = (record_dir / filename).resolve()

        if not file_path.is_relative_to(record_dir.resolve()):
            return jsonify({'error': 'Invalid path'}), 403

        if not file_path.exists():
            return jsonify({'error': 'File not found'}), 404

        if combat_parser and combat_parser.file_manager and combat_parser.file_manager.delete_recording(file_path, reason="user request"):
            return jsonify({'success': True, 'message': f'Deleted {filename}'})
        else:
            file_path.unlink()
            return jsonify({'success': True, 'message': f'Deleted {filename}'})

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/video/<path:filename>')
def serve_video(filename: str):
    """Serve a video file for preview."""
    from flask import send_file, abort

    record_dir = get_recording_directory()
    if not record_dir:
        abort(500)

    file_path = (record_dir / filename).resolve()
    resolved_record_dir = record_dir.resolve()

    if not file_path.is_relative_to(resolved_record_dir):
        abort(403)

    if not file_path.exists() or not file_path.is_file():
        abort(404)

    return send_file(file_path)

@app.route('/api/recordings/<path:filename>/metadata')
def get_recording_metadata(filename: str):
    """Return the companion JSON metadata for a recording, if it exists."""
    record_dir = get_recording_directory()
    if not record_dir:
        return jsonify({'error': 'Recording directory not available'}), 500

    # Build the path to the video file and check it's inside record_dir
    video_path = (record_dir / filename).resolve()
    if not video_path.is_relative_to(record_dir.resolve()):
        return jsonify({'error': 'Invalid path'}), 403

    # Companion JSON sits next to the video with the same stem
    json_path = video_path.with_suffix('.json')

    if not json_path.exists():
        return jsonify({'error': 'No metadata found'}), 404

    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return jsonify(data)
    except Exception as e:
        print(f"[RECORDINGS] Error reading metadata for {filename}: {e}")
        return jsonify({'error': str(e)}), 500

# ============================================================================
# Cloud Upload API Routes
# ============================================================================

@app.route('/api/cloud/status')
def get_cloud_status():
    """Get current cloud upload status."""
    if not cloud_manager:
        return jsonify({
            'enabled': False,
            'authenticated': False,
            'error': 'Cloud manager not initialized'
        })
    
    queue_status = cloud_manager.get_queue_status()
    storage_info = cloud_manager.get_storage_info()
    
    return jsonify({
        'enabled': True,
        'authenticated': cloud_manager.is_ready(),
        'provider': queue_status.get('provider', ''),
        'queue': queue_status,
        'storage': storage_info,
    })


@app.route('/api/cloud/test-connection', methods=['POST'])
async def test_cloud_connection():
    """Test cloud connection with provided credentials."""
    try:
        data = request.get_json()
        provider = data.get('provider', 'warcraft_recorder')
        
        if provider != 'warcraft_recorder':
            return jsonify({'error': 'Only Warcraft Recorder supported currently'}), 400
        
        username = data.get('username')
        password = data.get('password')
        guild = data.get('guild')
        
        if not username or not password or not guild:
            return jsonify({'error': 'Missing credentials'}), 400
        
        from cloud_upload import WarcraftRecorderCloud
        
        test_provider = WarcraftRecorderCloud(username, password, guild)
        success = await test_provider.authenticate()
        
        if success:
            storage_info = test_provider.get_storage_info()
            return jsonify({
                'success': True,
                'message': 'Connection successful',
                'storage': storage_info,
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Authentication failed'
            }), 401
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/cloud/reinitialize', methods=['POST'])
async def reinitialize_cloud():
    """Reinitialize cloud manager with current config."""
    global cloud_manager
    
    try:
        if cloud_manager:
            await cloud_manager.shutdown()
        
        await init_cloud_manager()
        
        if cloud_manager and cloud_manager.is_ready():
            return jsonify({
                'success': True,
                'message': 'Cloud manager reinitialized successfully'
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Failed to initialize cloud manager'
            }), 500
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/recordings/<path:filename>/upload', methods=['POST'])
def manual_upload_recording(filename: str):
    """Manually trigger upload for a specific recording."""
    if not cloud_manager or not cloud_manager.is_ready():
        return jsonify({'error': 'Cloud upload not available'}), 503
    
    try:
        record_dir = get_recording_directory()
        if not record_dir:
            return jsonify({'error': 'Recording directory not available'}), 500
        
        file_path = (record_dir / filename).resolve()
        
        if not file_path.is_relative_to(record_dir.resolve()):
            return jsonify({'error': 'Invalid path'}), 403
        
        if not file_path.exists():
            return jsonify({'error': 'File not found'}), 404
        
        # Parse metadata from filename
        stem = file_path.stem
        parts = stem.split('_')
        
        boss_name = None
        difficulty = None
        if len(parts) >= 4:
            boss_name = parts[2]
            difficulty = parts[3]
        
        success = cloud_manager.queue_upload(
            file_path=file_path,
            boss_name=boss_name,
            difficulty=difficulty,
            category='manual',
        )
        
        if success:
            return jsonify({
                'success': True,
                'message': f'Queued {filename} for upload'
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Failed to queue upload'
            }), 500
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# -----------------------------------------------------------------------------
# WebSocket Events
# -----------------------------------------------------------------------------

@socketio.on('connect')
def handle_connect(auth=None):
    """Handle client connection - send current status and event log.
    
    Args:
        auth: Optional authentication data (not used)
    """
    print(f"{LOG_PREFIXES['WEBSOCKET']} Client connected")
    status = build_status()
    emit('status', status)
    emit('event_log', event_log)


@socketio.on('request_status')
def handle_status_request(auth=None):
    """Handle explicit status request from client.
    
    Args:
        auth: Optional authentication data (not used)
    """
    print(f"{LOG_PREFIXES['WEBSOCKET']} Status requested by client")
    status = build_status()
    emit('status', status)


# -----------------------------------------------------------------------------
# Status
# -----------------------------------------------------------------------------


def build_status() -> dict:
    """Build current status dictionary."""
    recorder_state = {}
    if state_manager:
        summary = state_manager.summary()
        recorder_state = {
            'recording': summary.get('recording', False),
            'encounter_active': summary.get('encounter_active', False),
            'boss_name': summary.get('boss_name'),
            'boss_id': summary.get('boss_id'),
            'difficulty_id': summary.get('difficulty_id'),
            'encounter_duration': round(summary.get('encounter_duration', 0), 1),
            'recording_duration': round(summary.get('recording_duration', 0), 1),
            'dungeon_active': summary.get('dungeon_active', False),
            'dungeon_name': summary.get('dungeon_name'),
            'dungeon_level': summary.get('dungeon_level'),
        }

    monitor_state = {}
    if log_monitor:
        monitor_status = log_monitor.get_status()
        current_log = monitor_status.get('current_log')
        monitor_state = {
            'is_monitoring': monitor_status.get('is_monitoring', False),
            'is_tailing': monitor_status.get('is_tailing', False),
            'current_log': Path(current_log).name if current_log else None,
            'current_log_full': current_log,
            'directory': monitor_status.get('directory'),
        }

    return {
        'timestamp': time.time(),
        'recorder_running': recorder_running,
        'obs_connected': obs_client.is_connected if obs_client else False,
        'recorder': recorder_state,
        'log_monitor': monitor_state,
    }

def broadcast_cloud_status():
    """Broadcast current cloud upload status to all clients."""
    if not cloud_manager:
        status = {
            'enabled': False,
            'authenticated': False,
        }
    else:
        queue_status = cloud_manager.get_queue_status()
        storage_info = cloud_manager.get_storage_info()
        
        status = {
            'enabled': True,
            'authenticated': cloud_manager.is_ready(),
            'provider': queue_status.get('provider', ''),
            'queue_size': queue_status.get('queue_size', 0),
            'active_upload': queue_status.get('active_upload'),
            'completed_count': queue_status.get('completed_count', 0),
            'failed_count': queue_status.get('failed_count', 0),
            'storage': storage_info if storage_info else None,
        }
    
    socketio.emit('cloud_status', status)


def broadcast_upload_progress(progress: UploadProgress):
    """Broadcast upload progress to all clients."""
    data = {
        'video_name': progress.video_name,
        'progress_percent': round(progress.progress_percent, 1),
        'uploaded_mb': round(progress.uploaded_bytes / (1024**2), 1),
        'total_mb': round(progress.total_bytes / (1024**2), 1),
        'upload_speed': format_upload_speed(progress.upload_speed),
        'status': progress.status,
        'error': progress.error,
    }
    
    socketio.emit('upload_progress', data)


def format_upload_speed(bytes_per_sec: float) -> str:
    """Format upload speed for display."""
    if bytes_per_sec < 1024:
        return f"{bytes_per_sec:.0f} B/s"
    elif bytes_per_sec < 1024**2:
        return f"{bytes_per_sec / 1024:.1f} KB/s"
    else:
        return f"{bytes_per_sec / (1024**2):.1f} MB/s"

# -----------------------------------------------------------------------------
# Status Broadcast Loop
# -----------------------------------------------------------------------------

def status_broadcast_loop():
    """Background thread that broadcasts status updates."""
    last_status = None
    last_cloud_broadcast = 0

    while not shutdown_event.is_set():
        try:
            status = build_status()

            recorder = status.get('recorder') or {}
            log_mon = status.get('log_monitor') or {}
            status_key = (
                recorder.get('recording'),
                recorder.get('encounter_active'),
                recorder.get('boss_name'),
                recorder.get('dungeon_active'),
                recorder.get('dungeon_name'),
                log_mon.get('current_log'),
                status.get('obs_connected'),
            )

            if status_key != last_status:
                socketio.emit('status', status)
                last_status = status_key
            elif status['recorder'].get('recording'):
                # Keep updating duration while recording even if status_key unchanged
                socketio.emit('status', status)
            # Cloud stuff    
            current_time = time.time()
            if current_time - last_cloud_broadcast >= 5:
                broadcast_cloud_status()
                last_cloud_broadcast = current_time

        except Exception as e:
            print(f"{LOG_PREFIXES['WEBSOCKET']} Error: {e}")

        shutdown_event.wait(STATUS_BROADCAST_INTERVAL)



# -----------------------------------------------------------------------------
# Event Handling
# -----------------------------------------------------------------------------

def handle_combat_event(event: dict):
    """Handle combat events from the parser."""
    global event_log

    event_log.append(event)

    if len(event_log) > MAX_EVENT_LOG_SIZE:
        event_log = event_log[-MAX_EVENT_LOG_SIZE:]

    socketio.emit('combat_event', event)


def handle_recording_saved(recording_info: dict = None):
    """Handle recording saved event - notify clients to refresh recordings list."""
    socketio.emit('recordings_updated')

    if not (cloud_manager and cloud_manager.is_ready() and combat_parser):
        return

    info = recording_info or {}
    duration = info.get('duration', 0)

    if not should_auto_upload(config_manager, duration):
        return

    try:
        recordings = list_recording_files()
        if not recordings:
            print("[Cloud] No recordings found to upload")
            return

        record_dir = get_recording_directory()
        if not record_dir:
            print("[Cloud] Could not determine recording directory")
            return

        file_path = record_dir / recordings[0]['name']
        stem = file_path.stem  # filename without extension

        # Build VideoMetadata with fields matching the rewritten dataclass
        import hashlib, time as _time
        unique_hash = hashlib.md5(f"{stem}_{_time.time()}".encode()).hexdigest()

        from cloud_upload import VideoMetadata
        metadata = VideoMetadata(
            video_name=stem,
            video_key=file_path.name,
            file_path=str(file_path),
            file_size=file_path.stat().st_size,
            start=int(_time.time() * 1000),
            unique_hash=unique_hash,
            category='dungeon' if info.get('category') == 'dungeon' else 'Raids',
            encounter_name=info.get('boss_name'),
            difficulty_id=info.get('difficulty_id'),
            duration=int(duration),
            result=bool(info.get('is_kill', False)),
        )

        cloud_manager.queue_upload(file_path=file_path, metadata=metadata)
        print(f"[Cloud] Queued for upload: {file_path.name}")

    except Exception as e:
        print(f"[Cloud] Error queuing upload: {e}")

async def init_cloud_manager():
    """Initialize cloud upload manager asynchronously."""
    global cloud_manager
    
    if not config_manager.CLOUD_UPLOAD_ENABLED:
        print("[Cloud] Cloud upload disabled in config")
        return
    
    try:
        cloud_manager = await initialize_cloud_upload(config_manager)
        
        if cloud_manager:
            cloud_manager.set_progress_callback(broadcast_upload_progress)
            print("[Cloud] ✅ Cloud manager initialized")
        else:
            print("[Cloud] ❌ Failed to initialize cloud manager")
    except Exception as e:
        print(f"[Cloud] ❌ Initialization error: {e}")
        import traceback
        traceback.print_exc()

# -----------------------------------------------------------------------------
# Recorder Initialization
# -----------------------------------------------------------------------------

def init_recorder(config_path: Path) -> bool:
    """Initialize recorder components."""
    global config_manager, obs_client, state_manager, log_monitor, combat_parser, recorder_running

    try:
        config_manager = ConfigManager(config_path)

        print(f"{LOG_PREFIXES['RECORDER']} Connecting to OBS...")
        obs_client = OBSClient(
            host=config_manager.OBS_HOST,
            port=config_manager.OBS_PORT,
            password=config_manager.OBS_PASSWORD
        )

        if not obs_client.connect():
            print(f"{LOG_PREFIXES['RECORDER']} Warning: Could not connect to OBS")
            print(f"{LOG_PREFIXES['RECORDER']} Recording will not work until OBS is connected")
        else:
            print(f"{LOG_PREFIXES['RECORDER']} Connected to OBS")

        state_manager = RecordingState()

        combat_parser = CombatParser(obs_client, state_manager, config_manager)
        combat_parser.on_event = handle_combat_event  # This should point to handle_combat_event
        combat_parser.on_recording_saved = handle_recording_saved  # This should point to handle_recording_saved

        log_monitor = LogMonitor(config_manager.LOG_DIR, combat_parser)

        if config_manager.LOG_DIR.exists():
            log_monitor.start()
            print(f"{LOG_PREFIXES['RECORDER']} Monitoring: {config_manager.LOG_DIR}")
        else:
            print(f"")
            print(f"⚠️  {LOG_PREFIXES['RECORDER']} LOG DIRECTORY NOT FOUND")
            print(f"   Path: {config_manager.LOG_DIR}")
            print(f"   Please update 'log_dir' in your config.ini")
            print(f"")

        asyncio.run(init_cloud_manager())

        recorder_running = True
        return True

    except Exception as e:
        print(f"{LOG_PREFIXES['RECORDER']} Initialization error: {e}")
        return False

def shutdown_recorder():
    """Clean shutdown of recorder components."""
    global recorder_running, cloud_manager

    print("[RECORDER] Shutting down...")
    recorder_running = False

    if log_monitor:
        log_monitor.stop()

    # ADD THIS: Shutdown cloud manager
    if cloud_manager:
        print("[Cloud] Shutting down cloud manager...")
        try:
            asyncio.run(cloud_manager.shutdown())
        except Exception as e:
            print(f"[Cloud] Error during shutdown: {e}")

    if obs_client:
        obs_client.disconnect()

    print("[RECORDER] Shutdown complete")



# -----------------------------------------------------------------------------
# Main Entry Point
# -----------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='WoW Raid Recorder with Web frontend')
    parser.add_argument('--config', type=Path, default=Path('config.ini'),
                        help='Path to configuration file')
    parser.add_argument('--host', default=DEFAULT_WEB_HOST,
                        help=f'Web server host (default: {DEFAULT_WEB_HOST})')
    parser.add_argument('--port', type=int, default=DEFAULT_WEB_PORT,
                        help=f'Web server port (default: {DEFAULT_WEB_PORT})')
    parser.add_argument('--no-recorder', action='store_true',
                        help='Start web GUI only, without recorder')
    parser.add_argument('--debug', action='store_true',
                        help='Enable debug mode')

    args = parser.parse_args()

    def signal_handler(sig, frame):
        print("\n[APP] Shutdown requested...")
        shutdown_event.set()
        shutdown_recorder()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    if not args.no_recorder:
        if not init_recorder(args.config):
            print("[APP] Warning: Recorder initialization failed")
    else:
        global config_manager
        config_manager = ConfigManager(args.config)

    broadcast_thread = threading.Thread(target=status_broadcast_loop, daemon=True)
    broadcast_thread.start()

    print(f"[APP] Starting web server at http://{args.host}:{args.port}")
    socketio.run(app, host=args.host, port=args.port, debug=args.debug, allow_unsafe_werkzeug=True)


if __name__ == '__main__':
    main()
