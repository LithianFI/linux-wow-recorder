import os
import secrets
"""
Application-wide constants for WoW Raid Recorder.
"""

# ============================================================================
# FILE SYSTEM CONSTANTS
# ============================================================================

# Video file extensions (used by RecordingFileManager)
VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.flv', '.mov', '.ts', '.m3u8', '.avi', '.wmv'}

# Default file patterns
DEFAULT_LOG_PATTERN = r'WoWCombatLog-\d{6}_\d{6}\.txt$'
DEFAULT_RECORDING_EXTENSION = '.mp4'

# Default configuration paths
DEFAULT_CONFIG_NAME = 'config.ini'
DEFAULT_CREDENTIALS_NAME = 'credentials.json'
DEFAULT_TOKEN_NAME = 'token.json'

# ============================================================================
# WOW GAME CONSTANTS
# ============================================================================

# Difficulty ID mappings (used by ConfigManager)
DIFFICULTY_IDS = {
    'lfr': [7, 17],           # Looking For Raid
    'normal': [1, 14],        # Normal
    'heroic': [2, 15],        # Heroic
    'mythic': [3, 16, 23],    # Mythic
    'other': [4, 5, 8, 9, 24, 33],  # Timewalking, Mythic+, etc.
}

# Difficulty names for display (used by file_manager.py and frontend)
DIFFICULTY_NAMES = {
    1: "Normal", 
    2: "Heroic", 
    3: "Mythic", 
    4: "Mythic+",
    5: "Timewalking", 
    7: "LFR", 
    9: "40Player",
    14: "Normal", 
    15: "Heroic", 
    16: "Mythic", 
    17: "LFR",
    23: "Mythic", 
    24: "Timewalking", 
    33: "Timewalking",
}

# Combat log event types (used by combat_parser/events.py)
EVENT_TYPES = {
    'ENCOUNTER_START': 'ENCOUNTER_START',
    'ENCOUNTER_END': 'ENCOUNTER_END',
    'CHALLENGE_MODE_START': 'CHALLENGE_MODE_START',
    'CHALLENGE_MODE_END': 'CHALLENGE_MODE_END',
    'ZONE_CHANGE': 'ZONE_CHANGE',
}

# ============================================================================
# APPLICATION CONSTANTS
# ============================================================================

# Default timeouts and delays (in seconds)
DEFAULT_RENAME_DELAY = 3                    # Used by RecordingProcessor
DEFAULT_DUNGEON_TIMEOUT = 120               # Used by DungeonMonitor
DEFAULT_MIN_RECORDING_DURATION = 5          # Used by RecordingProcessor
DEFAULT_MAX_RENAME_ATTEMPTS = 10            # Used by RecordingFileManager
DEFAULT_FILE_STABILITY_CHECK_INTERVAL = 1.0 # Used by RecordingFileManager

# OBS WebSocket defaults
DEFAULT_OBS_HOST = 'localhost'              # Used by OBSClient
DEFAULT_OBS_PORT = 4455                     # Used by OBSClient
DEFAULT_OBS_TIMEOUT = 3                     # Used by OBSClient

# Web server defaults
DEFAULT_WEB_HOST = '0.0.0.0'                # Used by run.py
DEFAULT_WEB_PORT = 5001                     # Used by run.py
FLASK_SECRET_KEY = os.environ.get('WOW_RECORDER_SECRET') or secrets.token_hex(32)   # Used by run.py

# Event log size
MAX_EVENT_LOG_SIZE = 50                     # Used by run.py and AppState

# Broadcast interval (seconds)
STATUS_BROADCAST_INTERVAL = 0.5             # Used by status_broadcast_loop in run.py

# ============================================================================
# LOGGING PREFIXES (for consistent console output)
# ============================================================================

LOG_PREFIXES = {
    'APP': '[APP]',
    'PARSER': '[PARSER]',
    'STATE': '[STATE]',
    'FILE': '[FILE]',
    'OBS': '[OBS]',
    'MONITOR': '[MONITOR]',
    'CONFIG': '[CONFIG]',
    'RECORDER': '[RECORDER]',
    'WEBSOCKET': '[WEBSOCKET]',
    'DUNGEON': '[DUNGEON]',
    'PROC': '[PROC]',  # RecordingProcessor
    'WATCHER': '[WATCHER]',  # LogWatcher
}

# ============================================================================
# STATUS CONSTANTS
# ============================================================================

# Recording statuses
RECORDING_STATUS = {
    'IDLE': 'idle',
    'RECORDING': 'recording',
    'ENCOUNTER_ACTIVE': 'encounter_active',
    'DUNGEON_ACTIVE': 'dungeon_active',
}

# ============================================================================
# ERROR MESSAGES (for consistent error handling)
# ============================================================================

ERROR_MESSAGES = {
    'OBS_CONNECTION_FAILED': 'Failed to connect to OBS WebSocket',
    'LOG_DIR_NOT_FOUND': 'Log directory not found',
    'RECORDING_DIR_NOT_FOUND': 'Recording directory not found',
    'CONFIG_LOAD_FAILED': 'Failed to load configuration',
    'FILE_NOT_FOUND': 'File not found',
    'PERMISSION_DENIED': 'Permission denied',
}