"""
Configuration Manager for WoW Raid Recorder.
Handles loading, saving, and accessing configuration settings.
"""

import os
import re
import configparser
from pathlib import Path
from typing import Dict, Any, Optional, List, Set


class ConfigManager:
    """Manages application configuration from INI files."""
    
    # ---------------------------------------------------------------------
    # Constants and Defaults
    # ---------------------------------------------------------------------
    
    # Difficulty ID mappings for WoW combat logs
    DIFFICULTY_IDS = {
        'lfr': [7, 17],           # Looking For Raid
        'normal': [1, 14],        # Normal
        'heroic': [2, 15],        # Heroic
        'mythic': [3, 16, 23],    # Mythic
        'other': [4, 5, 8, 9, 24, 33],  # Timewalking, Mythic+, etc.
    }
    
    # Default configuration values
    DEFAULT_CONFIG = {
        'General': {
            'log_dir': str(Path.home() / "Games" / "World of Warcraft" / "_retail_" / "Logs"),
            'log_pattern': r'WoWCombatLog-\d{6}_\d{6}\.txt$',
            'recording_extension': '.mp4',
        },
        'OBS': {
            'host': 'localhost',
            'port': '4455',
            'password': '',
        },
        'Recording': {
            'auto_rename': 'true',
            'rename_delay': '3',
            'max_rename_attempts': '10',
            'min_recording_duration': '5',
            'delete_short_recordings': 'true',
        },
        'Difficulties': {
            'record_lfr': 'false',
            'record_normal': 'true',
            'record_heroic': 'true',
            'record_mythic': 'true',
            'record_other': 'false',
        },
        'BossNames': {},  # Empty by default
    }
    
    # ---------------------------------------------------------------------
    # Initialization
    # ---------------------------------------------------------------------
    
    def __init__(self, config_path: Optional[Path] = None):
        """Initialize configuration manager.
        
        Args:
            config_path: Optional path to config file. If None, uses default location.
        """
        self.config_path = config_path or self._get_default_config_path()
        self.config = configparser.ConfigParser(interpolation=None)
        self._load_configuration()
    
    # ---------------------------------------------------------------------
    # Configuration Loading and Saving
    # ---------------------------------------------------------------------
    
    def _get_default_config_path(self) -> Path:
        """Get default configuration file path."""
        # Try user's home directory first
        home_config = Path.home() / ".wow_raid_recorder.ini"
        if home_config.exists():
            return home_config
        
        # Fall back to current directory
        return Path.cwd() / "config.ini"
    
    def _load_configuration(self):
        """Load configuration from file, creating default if needed."""
        # Set defaults first
        self.config.read_dict(self.DEFAULT_CONFIG)
        
        # Try to load user configuration
        if self.config_path.exists():
            try:
                self.config.read(self.config_path)
                print(f"[CONFIG] Loaded configuration from: {self.config_path}")
            except configparser.Error as e:
                print(f"[CONFIG] Error parsing config file: {e}")
                print("[CONFIG] Creating fresh configuration...")
                self._create_default_config()
                self.config.read(self.config_path)
        else:
            print(f"[CONFIG] Configuration file not found.")
            self._create_default_config()
            self.config.read(self.config_path)
    
    def _create_default_config(self):
        """Create a default configuration file."""
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            
            config_content = self._generate_default_config_content()
            
            with open(self.config_path, 'w') as f:
                f.write(config_content)
                
            print(f"[CONFIG] Created default configuration at: {self.config_path}")
            print("[CONFIG] Please edit the file to match your setup.")
            
        except Exception as e:
            print(f"[CONFIG] Failed to create config file: {e}")
            import traceback
            traceback.print_exc()
    
    def _generate_default_config_content(self) -> str:
        """Generate content for default configuration file."""
        recording_path = self._get_default_recording_path()
        log_dir = str(Path.home() / "Games" / "World of Warcraft" / "_retail_" / "Logs")
        
        return f"""# WoW Raid Recorder Configuration
# ============================================
# Edit this file to match your setup, then run the program.

[General]
# Path to your WoW logs directory
log_dir = {log_dir}

# Pattern to match combat log files
log_pattern = WoWCombatLog-\\d{{6}}_\\d{{6}}\\.txt$

# Extension for recording files (must match OBS settings)
recording_extension = .mp4

[OBS]
# OBS WebSocket connection settings
host = localhost
port = 4455

# Leave empty if no password is set in OBS
password = 

[Recording]
# Automatically rename recordings based on boss encounters
auto_rename = true

# Delay in seconds before renaming (to ensure OBS finished writing)
rename_delay = 3

# Maximum attempts before giving up on finding the recording file
max_rename_attempts = 10

# Minimum recording duration in seconds
min_recording_duration = 5

# Delete recordings shorter than minimum duration
delete_short_recordings = true

# Fallback recording path if OBS directory cannot be detected
recording_path_fallback = {recording_path}

[Difficulties]
# Which raid difficulties to record
# Set to true to record, false to ignore

record_lfr = false
record_normal = true
record_heroic = true
record_mythic = true
record_other = false

[BossNames]
# Boss ID to name overrides (optional)
# Format: <boss_id> = <display_name>
# Example:
# 2688 = Rashok
# 2687 = The Vigilant Steward, Zskarn
"""
    
    def save(self):
        """Save current configuration to file."""
        try:
            with open(self.config_path, 'w') as f:
                self.config.write(f)
            print(f"[CONFIG] Configuration saved to: {self.config_path}")
        except Exception as e:
            print(f"[CONFIG] Failed to save config: {e}")
    
    # ---------------------------------------------------------------------
    # Path Handling
    # ---------------------------------------------------------------------
    
    def _get_default_recording_path(self) -> str:
        """Get default recording path based on OS."""
        home = Path.home()
        
        if os.name == 'nt':  # Windows
            return str(home / "Videos")
        elif os.name == 'posix':  # Linux/macOS
            return str(home / "Videos")
        else:
            return str(home)
    
    def _sanitize_path(self, path_str: str) -> Path:
        """Sanitize and normalize a path string."""
        if not path_str:
            return Path()
        
        # Clean the string
        path_str = path_str.strip().strip('"').strip("'")
        
        # Expand home directory
        if path_str.startswith('~'):
            path_str = str(Path.home()) + path_str[1:]
        
        # Normalize path
        return Path(os.path.normpath(path_str))
    
    # ---------------------------------------------------------------------
    # Configuration Access (Properties)
    # ---------------------------------------------------------------------
    
    # General settings
    @property
    def LOG_DIR(self) -> Path:
        """Get log directory path."""
        path_str = self.config.get('General', 'log_dir', fallback='', raw=True)
        return self._sanitize_path(path_str)
    
    @property
    def LOG_PATTERN(self) -> re.Pattern:
        """Get compiled log file pattern."""
        pattern = self.config.get('General', 'log_pattern', 
                                 fallback=r'WoWCombatLog-\d{6}_\d{6}\.txt$', raw=True)
        return re.compile(pattern)
    
    @property
    def RECORDING_EXTENSION(self) -> str:
        """Get recording file extension."""
        return self.config.get('General', 'recording_extension', fallback='.mp4', raw=True)
    
    # OBS settings
    @property
    def OBS_HOST(self) -> str:
        """Get OBS WebSocket host."""
        return self.config.get('OBS', 'host', fallback='localhost', raw=True)
    
    @property
    def OBS_PORT(self) -> int:
        """Get OBS WebSocket port."""
        return self.config.getint('OBS', 'port', fallback=4455)
    
    @property
    def OBS_PASSWORD(self) -> str:
        """Get OBS WebSocket password."""
        return self.config.get('OBS', 'password', fallback='', raw=True)
    
    # Recording settings
    @property
    def AUTO_RENAME(self) -> bool:
        """Check if auto-rename is enabled."""
        return self.config.getboolean('Recording', 'auto_rename', fallback=True)
    
    @property
    def RENAME_DELAY(self) -> int:
        """Get rename delay in seconds."""
        return self.config.getint('Recording', 'rename_delay', fallback=3)
    
    @property
    def MAX_RENAME_ATTEMPTS(self) -> int:
        """Get maximum rename attempts."""
        return self.config.getint('Recording', 'max_rename_attempts', fallback=10)
    
    @property
    def MIN_RECORDING_DURATION(self) -> int:
        """Get minimum recording duration in seconds."""
        return self.config.getint('Recording', 'min_recording_duration', fallback=5)
    
    @property
    def DELETE_SHORT_RECORDINGS(self) -> bool:
        """Check if short recordings should be deleted."""
        return self.config.getboolean('Recording', 'delete_short_recordings', fallback=True)
    
    @property
    def RECORDING_PATH_FALLBACK(self) -> Optional[Path]:
        """Get fallback recording path."""
        path_str = self.config.get('Recording', 'recording_path_fallback', fallback='', raw=True)
        if path_str and path_str.strip():
            return self._sanitize_path(path_str.strip())
        return None
    
    # Difficulty settings
    @property
    def RECORD_LFR(self) -> bool:
        """Check if LFR should be recorded."""
        return self.config.getboolean('Difficulties', 'record_lfr', fallback=False)
    
    @property
    def RECORD_NORMAL(self) -> bool:
        """Check if Normal difficulty should be recorded."""
        return self.config.getboolean('Difficulties', 'record_normal', fallback=True)
    
    @property
    def RECORD_HEROIC(self) -> bool:
        """Check if Heroic difficulty should be recorded."""
        return self.config.getboolean('Difficulties', 'record_heroic', fallback=True)
    
    @property
    def RECORD_MYTHIC(self) -> bool:
        """Check if Mythic difficulty should be recorded."""
        return self.config.getboolean('Difficulties', 'record_mythic', fallback=True)
    
    @property
    def RECORD_OTHER(self) -> bool:
        """Check if other difficulties should be recorded."""
        return self.config.getboolean('Difficulties', 'record_other', fallback=False)
    
    # ---------------------------------------------------------------------
    # Difficulty Management
    # ---------------------------------------------------------------------
    
    def get_enabled_difficulties(self) -> Set[int]:
        """Get set of enabled difficulty IDs."""
        enabled = set()
        
        if self.RECORD_LFR:
            enabled.update(self.DIFFICULTY_IDS['lfr'])
        if self.RECORD_NORMAL:
            enabled.update(self.DIFFICULTY_IDS['normal'])
        if self.RECORD_HEROIC:
            enabled.update(self.DIFFICULTY_IDS['heroic'])
        if self.RECORD_MYTHIC:
            enabled.update(self.DIFFICULTY_IDS['mythic'])
        if self.RECORD_OTHER:
            enabled.update(self.DIFFICULTY_IDS['other'])
        
        return enabled
    
    def is_difficulty_enabled(self, difficulty_id: int) -> bool:
        """Check if a specific difficulty ID is enabled."""
        return difficulty_id in self.get_enabled_difficulties()
    
    # ---------------------------------------------------------------------
    # Boss Name Management
    # ---------------------------------------------------------------------
    
    @property
    def BOSS_NAME_OVERRIDES(self) -> Dict[int, str]:
        """Get boss name overrides from config."""
        overrides = {}
        if 'BossNames' in self.config:
            for key, value in self.config.items('BossNames', raw=True):
                try:
                    boss_id = int(key)
                    overrides[boss_id] = value
                except ValueError:
                    continue
        return overrides
    
    def set_boss_name_override(self, boss_id: int, name: str):
        """Set a boss name override."""
        if 'BossNames' not in self.config:
            self.config.add_section('BossNames')
        self.config.set('BossNames', str(boss_id), name)
        self.save()
    
    # ---------------------------------------------------------------------
    # Utility Methods
    # ---------------------------------------------------------------------
    
    def get(self, section: str, key: str, fallback: Any = None) -> Any:
        """Generic getter for configuration values."""
        try:
            return self.config.get(section, key, fallback=fallback, raw=True)
        except (configparser.NoSectionError, configparser.NoOptionError):
            return fallback
    
    def set(self, section: str, key: str, value: str):
        """Generic setter for configuration values."""
        if section not in self.config:
            self.config.add_section(section)
        self.config.set(section, key, value)
        self.save()
    
    def validate(self) -> Dict[str, List[str]]:
        """Validate configuration and return any errors."""
        errors = {}
        
        # Check log directory
        if not self.LOG_DIR.exists():
            errors.setdefault('General', []).append(
                f"Log directory does not exist: {self.LOG_DIR}"
            )
        
        # Check OBS connection
        if not self.OBS_HOST:
            errors.setdefault('OBS', []).append("OBS host cannot be empty")
        
        # Check recording extension
        if not self.RECORDING_EXTENSION.startswith('.'):
            errors.setdefault('General', []).append(
                f"Recording extension must start with '.': {self.RECORDING_EXTENSION}"
            )
        
        return errors
    
    def print_summary(self):
        """Print configuration summary."""
        print("\n" + "="*60)
        print("Configuration Summary")
        print("="*60)
        
        for section in self.config.sections():
            print(f"\n[{section}]")
            for key, value in self.config.items(section, raw=True):
                # Hide passwords
                if 'password' in key.lower() and value:
                    print(f"  {key} = [HIDDEN]")
                else:
                    print(f"  {key} = {value}")
        
        # Difficulty summary
        print("\n[Enabled Difficulties]")
        enabled = self.get_enabled_difficulties()
        print(f"  • LFR: {'✓' if self.RECORD_LFR else '✗'}")
        print(f"  • Normal: {'✓' if self.RECORD_NORMAL else '✗'}")
        print(f"  • Heroic: {'✓' if self.RECORD_HEROIC else '✗'}")
        print(f"  • Mythic: {'✓' if self.RECORD_MYTHIC else '✗'}")
        print(f"  • Other: {'✓' if self.RECORD_OTHER else '✗'}")
        print(f"  • Total IDs: {len(enabled)}")
        
        print("="*60)