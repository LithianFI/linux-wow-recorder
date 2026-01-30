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
from pathlib import Path

from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit

from config_manager import ConfigManager
from obs_client import OBSClient
from state_manager import RecordingState
from combat_parser import CombatParser
from log_watcher import LogMonitor


# -----------------------------------------------------------------------------
# Flask App Setup
# -----------------------------------------------------------------------------

app = Flask(__name__)
app.config['SECRET_KEY'] = 'wow-recorder-secret'
socketio = SocketIO(app, cors_allowed_origins="*")

config_manager: ConfigManager = None
obs_client: OBSClient = None
state_manager: RecordingState = None
log_monitor: LogMonitor = None
combat_parser: CombatParser = None
recorder_running = False
shutdown_event = threading.Event()
event_log: list = []
MAX_EVENT_LOG_SIZE = 50


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
        },
        'difficulties': {
            'record_lfr': config_manager.RECORD_LFR,
            'record_normal': config_manager.RECORD_NORMAL,
            'record_heroic': config_manager.RECORD_HEROIC,
            'record_mythic': config_manager.RECORD_MYTHIC,
            'record_other': config_manager.RECORD_OTHER,
        },
        'boss_names': config_manager.BOSS_NAME_OVERRIDES,
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


# -----------------------------------------------------------------------------
# WebSocket Events
# -----------------------------------------------------------------------------

@socketio.on('connect')
def handle_connect():
    """Handle client connection - send current status and event log."""
    status = build_status()
    emit('status', status)
    emit('event_log', event_log)


@socketio.on('request_status')
def handle_status_request():
    """Handle explicit status request from client."""
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


# -----------------------------------------------------------------------------
# Status Broadcast Loop
# -----------------------------------------------------------------------------

def status_broadcast_loop():
    """Background thread that broadcasts status updates."""
    last_status = None

    while not shutdown_event.is_set():
        try:
            status = build_status()

            recorder = status.get('recorder') or {}
            log_mon = status.get('log_monitor') or {}
            status_key = (
                recorder.get('recording'),
                recorder.get('encounter_active'),
                recorder.get('boss_name'),
                log_mon.get('current_log'),
                status.get('obs_connected'),
            )

            if status_key != last_status:
                socketio.emit('status', status)
                last_status = status_key
            elif status['recorder'].get('recording'):
                # Keep updating duration while recording even if status_key unchanged
                socketio.emit('status', status)

        except Exception as e:
            print(f"[BROADCAST] Error: {e}")

        shutdown_event.wait(0.5)


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


def handle_recording_saved():
    """Handle recording saved event - notify clients to refresh recordings list."""
    socketio.emit('recordings_updated')


# -----------------------------------------------------------------------------
# Recorder Initialization
# -----------------------------------------------------------------------------

def init_recorder(config_path: Path) -> bool:
    """Initialize recorder components.

    Returns:
        True if successful, False otherwise
    """
    global config_manager, obs_client, state_manager, log_monitor, combat_parser, recorder_running

    try:
        config_manager = ConfigManager(config_path)

        print("[RECORDER] Connecting to OBS...")
        obs_client = OBSClient(
            host=config_manager.OBS_HOST,
            port=config_manager.OBS_PORT,
            password=config_manager.OBS_PASSWORD
        )

        if not obs_client.connect():
            print("[RECORDER] Warning: Could not connect to OBS")
            print("[RECORDER] Recording will not work until OBS is connected")
        else:
            print("[RECORDER] Connected to OBS")

        state_manager = RecordingState()

        combat_parser = CombatParser(obs_client, state_manager, config_manager)
        combat_parser.on_event = handle_combat_event
        combat_parser.on_recording_saved = handle_recording_saved

        log_monitor = LogMonitor(config_manager.LOG_DIR, combat_parser)

        if config_manager.LOG_DIR.exists():
            log_monitor.start()
            print(f"[RECORDER] Monitoring: {config_manager.LOG_DIR}")
        else:
            print(f"")
            print(f"⚠️  [RECORDER] LOG DIRECTORY NOT FOUND")
            print(f"   Path: {config_manager.LOG_DIR}")
            print(f"   Please update 'log_dir' in your config.ini")
            print(f"")

        recorder_running = True
        return True

    except Exception as e:
        print(f"[RECORDER] Initialization error: {e}")
        return False


def shutdown_recorder():
    """Clean shutdown of recorder components."""
    global recorder_running

    print("[RECORDER] Shutting down...")
    recorder_running = False

    if log_monitor:
        log_monitor.stop()

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
    parser.add_argument('--host', default='0.0.0.0',
                        help='Web server host (default: 0.0.0.0)')
    parser.add_argument('--port', type=int, default=5001,
                        help='Web server port (default: 5001)')
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
