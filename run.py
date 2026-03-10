#!/usr/bin/env python3
"""
WoW Raid Recorder with Web frontend.
Runs the recorder and web interface in a single process with WebSocket communication.
"""

import sys
import json
import time
import signal
import argparse
import threading
import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from collections import deque

from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit

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


# -----------------------------------------------------------------------------
# Application State
# -----------------------------------------------------------------------------

@dataclass
class AppState:
    """Holds all mutable runtime state for the application."""
    config_manager: Optional[ConfigManager] = None
    obs_client: Optional[OBSClient] = None
    state_manager: Optional[RecordingState] = None
    log_monitor: Optional[LogMonitor] = None
    combat_parser: Optional[CombatParser] = None
    cloud_manager: Optional[CloudUploadManager] = None
    recorder_running: bool = False
    event_log: deque = field(default_factory=lambda: deque(maxlen=MAX_EVENT_LOG_SIZE))


# -----------------------------------------------------------------------------
# Flask App Setup
# -----------------------------------------------------------------------------

app = Flask(__name__)
app.config['SECRET_KEY'] = FLASK_SECRET_KEY
app.config['state'] = AppState()
socketio = SocketIO(app, cors_allowed_origins="*")

# Single process-level event — legitimately not part of AppState
shutdown_event = threading.Event()


def get_state() -> AppState:
    """Accessor for the application state stored on the Flask app."""
    return app.config['state']


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

@app.route('/api/recording/start', methods=['POST'])
def start_manual_recording():
    """Start a manual recording session."""
    s = get_state()
    if not s.combat_parser:
        return jsonify({'error': 'Recorder not initialized'}), 503
    if s.state_manager and s.state_manager.is_recording:
        return jsonify({'error': 'A recording is already active'}), 409
    success = s.combat_parser.start_manual_recording()
    if success:
        return jsonify({'success': True, 'message': 'Manual recording started'})
    return jsonify({'success': False, 'error': 'Failed to start recording'}), 500

@app.route('/api/recording/stop', methods=['POST'])
def stop_manual_recording():
    """Stop an active manual recording session."""
    s = get_state()
    if not s.combat_parser:
        return jsonify({'error': 'Recorder not initialized'}), 503
    if not (s.state_manager and s.state_manager.manual_recording):
        return jsonify({'error': 'No manual recording is active'}), 409
    success = s.combat_parser.stop_manual_recording()
    if success:
        return jsonify({'success': True, 'message': 'Manual recording stopped'})
    return jsonify({'success': False, 'error': 'Failed to stop recording'}), 500


@app.route('/api/config', methods=['GET'])
def get_config():
    """Return current configuration as JSON."""
    s = get_state()
    if s.config_manager is None:
        return jsonify({'error': 'Config not initialized'}), 500

    cm = s.config_manager
    config_data = {
        'general': {
            'log_dir': str(cm.LOG_DIR),
            'log_pattern': cm.config.get('General', 'log_pattern', raw=True),
            'recording_extension': cm.RECORDING_EXTENSION,
        },
        'obs': {
            'host': cm.OBS_HOST,
            'port': cm.OBS_PORT,
            'password': cm.OBS_PASSWORD,
        },
        'recording': {
            'auto_rename': cm.AUTO_RENAME,
            'rename_delay': cm.RENAME_DELAY,
            'max_rename_attempts': cm.MAX_RENAME_ATTEMPTS,
            'min_recording_duration': cm.MIN_RECORDING_DURATION,
            'delete_short_recordings': cm.DELETE_SHORT_RECORDINGS,
            'recording_path_fallback': str(cm.RECORDING_PATH_FALLBACK or ''),
            'dungeon_timeout_seconds': cm.DUNGEON_TIMEOUT_SECONDS,
            'file_naming_scheme': cm.FILE_NAMING_SCHEME,
            'generate_metadata_json': cm.GENERATE_METADATA_JSON,
            'track_player_deaths': cm.TRACK_PLAYER_DEATHS,
            'organize_by_date': cm.ORGANIZE_BY_DATE,
        },
        'difficulties': {
            'record_lfr': cm.RECORD_LFR,
            'record_normal': cm.RECORD_NORMAL,
            'record_heroic': cm.RECORD_HEROIC,
            'record_mythic': cm.RECORD_MYTHIC,
            'record_other': cm.RECORD_OTHER,
            'record_mplus': cm.RECORD_MPLUS,
        },
        'boss_names': cm.BOSS_NAME_OVERRIDES,
        'cloud_upload': {
            'enabled': cm.CLOUD_UPLOAD_ENABLED,
            'provider': cm.CLOUD_UPLOAD_PROVIDER,
            'auto_upload': cm.CLOUD_AUTO_UPLOAD,
            'delete_after_upload': cm.CLOUD_DELETE_AFTER_UPLOAD,
            'upload_on_startup': cm.CLOUD_UPLOAD_ON_STARTUP,
            'wcr_username': cm.WCR_USERNAME,
            'wcr_password': cm.WCR_PASSWORD,
            'wcr_guild': cm.WCR_GUILD,
        },
    }

    return jsonify(config_data)


@app.route('/api/config', methods=['POST'])
def save_config():
    """Save configuration changes."""
    s = get_state()
    if s.config_manager is None:
        return jsonify({'error': 'Config not initialized'}), 500

    try:
        data = request.get_json()
        s.config_manager.update_from_dict(data)
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


def get_recording_directory() -> Optional[Path]:
    """Get the recordings directory, with fallback to config."""
    s = get_state()
    if s.combat_parser and s.combat_parser.file_manager:
        try:
            record_dir = s.combat_parser.file_manager.get_recording_directory()
            if record_dir and record_dir.exists():
                return record_dir
        except Exception as e:
            print(f"[RECORDINGS] Error getting directory from file_manager: {e}")

    if s.config_manager and s.config_manager.RECORDING_PATH_FALLBACK:
        fallback = s.config_manager.RECORDING_PATH_FALLBACK
        if fallback.exists():
            return fallback

    return None


def list_recording_files() -> list:
    """List all recording files in the recordings directory (including date subfolders)."""
    s = get_state()
    record_dir = get_recording_directory()
    if not record_dir or not record_dir.exists():
        return []

    ext = s.config_manager.RECORDING_EXTENSION.lower() if s.config_manager else '.mp4'
    recordings = []

    for file in record_dir.rglob(f'*{ext}'):
        if file.is_file():
            stat = file.stat()
            recordings.append({
                'name': str(file.relative_to(record_dir)),
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
        s = get_state()
        record_dir = get_recording_directory()
        if not record_dir:
            return jsonify({'error': 'Recording directory not available'}), 500

        file_path = (record_dir / filename).resolve()

        if not file_path.is_relative_to(record_dir.resolve()):
            return jsonify({'error': 'Invalid path'}), 403

        if not file_path.exists():
            return jsonify({'error': 'File not found'}), 404

        if s.combat_parser and s.combat_parser.file_manager and s.combat_parser.file_manager.delete_recording(file_path, reason="user request"):
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

    video_path = (record_dir / filename).resolve()
    if not video_path.is_relative_to(record_dir.resolve()):
        return jsonify({'error': 'Invalid path'}), 403

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
    s = get_state()
    if not s.cloud_manager:
        return jsonify({
            'enabled': False,
            'authenticated': False,
            'error': 'Cloud manager not initialized'
        })

    queue_status = s.cloud_manager.get_queue_status()
    storage_info = s.cloud_manager.get_storage_info()

    return jsonify({
        'enabled': True,
        'authenticated': s.cloud_manager.is_ready(),
        'provider': queue_status.get('provider', ''),
        'queue': queue_status,
        'storage': storage_info,
    })


@app.route('/api/cloud/test-connection', methods=['POST'])
def test_cloud_connection():
    """Test cloud connection with provided credentials."""
    s = get_state()
    if not s.config_manager:
        return jsonify({'error': 'Config not initialized'}), 500

    try:
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(_test_cloud_connection_async())
        loop.close()
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


async def _test_cloud_connection_async():
    """Async implementation of cloud connection test."""
    s = get_state()
    try:
        manager = await initialize_cloud_upload(s.config_manager)
        if manager:
            return {'success': True, 'message': 'Connection successful'}
        return {'success': False, 'error': 'Failed to connect'}
    except Exception as e:
        return {'success': False, 'error': str(e)}


@app.route('/api/cloud/upload/<path:filename>', methods=['POST'])
def queue_cloud_upload(filename: str):
    """Queue a specific recording for cloud upload."""
    s = get_state()
    if not s.cloud_manager or not s.cloud_manager.is_ready():
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

        stem = file_path.stem
        parts = stem.split('_')

        boss_name = None
        difficulty = None
        if len(parts) >= 4:
            boss_name = parts[2]
            difficulty = parts[3]

        success = s.cloud_manager.queue_upload(
            file_path=file_path,
            boss_name=boss_name,
            difficulty=difficulty,
            category='manual',
        )

        if success:
            return jsonify({'success': True, 'message': f'Queued {filename} for upload'})
        else:
            return jsonify({'success': False, 'error': 'Failed to queue upload'}), 500

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# -----------------------------------------------------------------------------
# WebSocket Events
# -----------------------------------------------------------------------------

@socketio.on('connect')
def handle_connect(auth=None):
    """Handle client connection - send current status and event log."""
    s = get_state()
    print(f"{LOG_PREFIXES['WEBSOCKET']} Client connected")
    status = build_status()
    emit('status', status)
    emit('event_log', list(s.event_log))


@socketio.on('request_status')
def handle_status_request(auth=None):
    """Handle explicit status request from client."""
    print(f"{LOG_PREFIXES['WEBSOCKET']} Status requested by client")
    status = build_status()
    emit('status', status)


# -----------------------------------------------------------------------------
# Status
# -----------------------------------------------------------------------------

def build_status() -> dict:
    """Build current status dictionary."""
    s = get_state()

    recorder_state = {}
    if s.state_manager:
        summary = s.state_manager.summary()
        recorder_state = {
            'recording': summary.get('recording', False),
            'manual_recording': summary.get('manual_recording', False),
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
    if s.log_monitor:
        monitor_status = s.log_monitor.get_status()
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
        'recorder_running': s.recorder_running,
        'obs_connected': s.obs_client.is_connected if s.obs_client else False,
        'recorder': recorder_state,
        'log_monitor': monitor_state,
    }


def broadcast_cloud_status():
    """Broadcast current cloud upload status to all clients."""
    s = get_state()
    if not s.cloud_manager:
        status = {'enabled': False, 'authenticated': False}
    else:
        queue_status = s.cloud_manager.get_queue_status()
        storage_info = s.cloud_manager.get_storage_info()
        status = {
            'enabled': True,
            'authenticated': s.cloud_manager.is_ready(),
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
                socketio.emit('status', status)

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
    s = get_state()
    s.event_log.append(event)
    socketio.emit('combat_event', event)


def handle_recording_saved(recording_info: dict = None):
    """Handle recording saved event — notify clients and trigger cloud upload."""
    s = get_state()
    socketio.emit('recordings_updated')

    if not (s.cloud_manager and s.cloud_manager.is_ready() and s.combat_parser):
        return

    info = recording_info or {}
    duration = info.get('duration', 0)

    if not should_auto_upload(s.config_manager, duration):
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

        parsed_meta = s.combat_parser.current_metadata

        from cloud_upload import VideoMetadata

        stem = file_path.stem
        video_key = file_path.name

        metadata = VideoMetadata(
            video_name=stem,
            video_key=video_key,
            file_path=str(file_path),
            file_size=file_path.stat().st_size,
            start=parsed_meta.start_timestamp or int(time.time() * 1000),
            unique_hash=parsed_meta.unique_hash or '',
            category=parsed_meta.category,
            flavour=parsed_meta.flavour,
            encounter_name=parsed_meta.encounter_name,
            encounter_id=parsed_meta.encounter_id,
            difficulty_id=parsed_meta.difficulty_id,
            difficulty=parsed_meta.difficulty,
            duration=parsed_meta.duration or int(duration),
            result=parsed_meta.result,
            boss_percent=parsed_meta.boss_percent,
            zone_id=parsed_meta.zone_id,
            zone_name=parsed_meta.zone_name,
            player=parsed_meta.player_info or None,
            combatants=parsed_meta.combatants or [],
            deaths=parsed_meta.deaths or [],
            overrun=parsed_meta.overrun,
            app_version=parsed_meta.app_version,
            keystone_level=getattr(parsed_meta, 'keystone_level', None),
            map_id=getattr(parsed_meta, 'map_id', None),
            upgrade_level=getattr(parsed_meta, 'upgrade_level', 0),
            affixes=getattr(parsed_meta, 'affixes', None),
        )

        s.cloud_manager.queue_upload(file_path=file_path, metadata=metadata)
        print(f"[Cloud] Queued for upload: {file_path.name}")

    except Exception as e:
        print(f"[Cloud] Error queuing upload: {e}")


async def init_cloud_manager():
    """Initialize cloud upload manager asynchronously."""
    s = get_state()

    if not s.config_manager.CLOUD_UPLOAD_ENABLED:
        print("[Cloud] Cloud upload disabled in config")
        return

    try:
        s.cloud_manager = await initialize_cloud_upload(s.config_manager)

        if s.cloud_manager:
            s.cloud_manager.set_progress_callback(broadcast_upload_progress)
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
    s = get_state()

    try:
        s.config_manager = ConfigManager(config_path)

        print(f"{LOG_PREFIXES['RECORDER']} Connecting to OBS...")
        s.obs_client = OBSClient(
            host=s.config_manager.OBS_HOST,
            port=s.config_manager.OBS_PORT,
            password=s.config_manager.OBS_PASSWORD
        )

        if not s.obs_client.connect():
            print(f"{LOG_PREFIXES['RECORDER']} Warning: Could not connect to OBS")
            print(f"{LOG_PREFIXES['RECORDER']} Recording will not work until OBS is connected")
        else:
            print(f"{LOG_PREFIXES['RECORDER']} Connected to OBS")

        s.state_manager = RecordingState()

        s.combat_parser = CombatParser(s.obs_client, s.state_manager, s.config_manager)
        s.combat_parser.on_event = handle_combat_event
        s.combat_parser.on_recording_saved = handle_recording_saved

        s.log_monitor = LogMonitor(s.config_manager.LOG_DIR, s.combat_parser)

        s.combat_parser.get_log_path = lambda: s.log_monitor.handler.current_log if s.log_monitor.handler else None

        if s.config_manager.LOG_DIR.exists():
            s.log_monitor.start()
            print(f"{LOG_PREFIXES['RECORDER']} Monitoring: {s.config_manager.LOG_DIR}")
        else:
            print(f"")
            print(f"⚠️  {LOG_PREFIXES['RECORDER']} LOG DIRECTORY NOT FOUND")
            print(f"   Path: {s.config_manager.LOG_DIR}")
            print(f"   Please update 'log_dir' in your config.ini")
            print(f"")

        asyncio.run(init_cloud_manager())

        s.recorder_running = True
        return True

    except Exception as e:
        print(f"{LOG_PREFIXES['RECORDER']} Initialization error: {e}")
        return False


def shutdown_recorder():
    """Clean shutdown of recorder components."""
    s = get_state()

    print("[RECORDER] Shutting down...")
    s.recorder_running = False

    if s.log_monitor:
        s.log_monitor.stop()

    if s.cloud_manager:
        print("[Cloud] Shutting down cloud manager...")
        try:
            asyncio.run(s.cloud_manager.shutdown())
        except Exception as e:
            print(f"[Cloud] Error during shutdown: {e}")

    if s.obs_client:
        s.obs_client.disconnect()

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
        get_state().config_manager = ConfigManager(args.config)

    broadcast_thread = threading.Thread(target=status_broadcast_loop, daemon=True)
    broadcast_thread.start()

    print(f"[APP] Starting web server at http://{args.host}:{args.port}")
    socketio.run(app, host=args.host, port=args.port, debug=args.debug, allow_unsafe_werkzeug=True)


if __name__ == '__main__':
    main()
