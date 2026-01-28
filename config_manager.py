# config_manager.py (fixed interpolation issue)
import os
import re
import configparser
from pathlib import Path
from typing import Dict, Any, Optional, List

class ConfigManager:
    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or self._get_default_config_path()
        # Create configparser WITHOUT interpolation to avoid issues with paths
        self.config = configparser.ConfigParser(interpolation=None)
        self._load_config()
    
    def _get_default_config_path(self) -> Path:
        """Get the default configuration file path"""
        # Try user's home directory first
        home_config = Path.home() / ".wow_raid_recorder.ini"
        if home_config.exists():
            return home_config
        
        # Fall back to current directory
        return Path.cwd() / "config.ini"
    
    def _load_config(self):
        """Load configuration from file, create default if doesn't exist"""
        # Default configuration
        default_config = {
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
                'rename_delay': '3',  # seconds
                'max_rename_attempts': '10',
                'min_recording_duration': '5',  # Minimum seconds to save a recording
                # Default OBS recording paths for different OS
                'recording_path_fallback': self._get_default_recording_path(),
            },
            'BossNames': {
                # These will be added dynamically based on overrides
            }
        }
        
        # Set defaults
        self.config.read_dict(default_config)
        
        # Try to load user configuration
        if self.config_path.exists():
            try:
                # Read with interpolation disabled
                self.config.read(self.config_path)
                print(f"[CONFIG] Loaded configuration from: {self.config_path}")
            except configparser.Error as e:
                print(f"[CONFIG] Error parsing config file: {e}")
                print("[CONFIG] The config file might have syntax errors.")
                print("[CONFIG] Try removing it or use --create-config to generate a fresh one.")
                raise
            except Exception as e:
                print(f"[CONFIG] Error loading config file: {e}")
                print("[CONFIG] Creating fresh config file...")
                self._create_default_config()
                # Re-read the fresh config
                self.config.read(self.config_path)
        else:
            print(f"[CONFIG] Configuration file not found. Creating default at: {self.config_path}")
            self._create_default_config()
    
    def _get_default_recording_path(self) -> str:
        """Get default recording path based on OS"""
        home = Path.home()
        
        # Default OBS recording paths by OS
        if os.name == 'nt':  # Windows
            return str(home / "Videos")
        elif os.name == 'posix':  # Linux/macOS
            return str(home / "Videos")
        else:
            return str(home)
    
    def _create_default_config(self):
        """Create a default configuration file with proper escaping"""
        try:
            # Ensure the directory exists
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Use raw strings for paths to avoid interpolation issues
            config_content = """# WoW Raid Recorder Configuration
# ================================
# Important: Do NOT use % signs in this file unless they are part of a path
# Paths with spaces do not need quotes

[General]
# Path to your WoW logs directory
log_dir = {log_dir}
# Pattern to match combat log files
log_pattern = WoWCombatLog-\\d{{6}}_\\d{{6}}\\.txt$
# Extension for recording files (must match OBS settings)
recording_extension = .mp4

[OBS]
# OBS WebSocket connection settings
# Leave password empty if no password is set in OBS
host = localhost
port = 4455
password = 

[Recording]
# Automatically rename recordings based on boss encounters
auto_rename = true
# Delay in seconds before renaming (to ensure OBS finished writing)
rename_delay = 3
# Maximum attempts before giving up on finding the recording file
max_rename_attempts = 10
# Minimum seconds to save a recording
min_recording_duration: 5  
# Fallback recording path if OBS recording directory cannot be detected
# Set to empty to always use OBS detected directory
recording_path_fallback = {recording_path}

[BossNames]
# Boss ID to name overrides (optional)
# Format: <boss_id> = <display_name>
# Example:
# 2688 = Rashok
# 2687 = The Vigilant Steward, Zskarn
""".format(
    log_dir=str(Path.home() / "Games" / "World of Warcraft" / "_retail_" / "Logs"),
    recording_path=self._get_default_recording_path()
)
            
            with open(self.config_path, 'w') as f:
                f.write(config_content)
                
            print(f"[CONFIG] Created default configuration file at: {self.config_path}")
            print("[CONFIG] Please edit the config file to match your setup before running.")
            
        except Exception as e:
            print(f"[CONFIG] Failed to create config file: {e}")
            import traceback
            traceback.print_exc()
    
    def _sanitize_path(self, path_str: str) -> Path:
        """Sanitize and normalize a path string"""
        if not path_str:
            return Path()
        
        # Remove any surrounding quotes
        path_str = path_str.strip().strip('"').strip("'")
        
        # Expand user home directory
        if path_str.startswith('~'):
            path_str = str(Path.home()) + path_str[1:]
        
        # Normalize path separators
        path_str = os.path.normpath(path_str)
        
        return Path(path_str)
    
    def save_config(self):
        """Save current configuration to file"""
        try:
            # Create a fresh configparser without interpolation for saving
            save_config = configparser.ConfigParser(interpolation=None)
            
            # Copy all sections
            for section in self.config.sections():
                save_config[section] = {}
                for key, value in self.config.items(section):
                    save_config[section][key] = value
            
            with open(self.config_path, 'w') as f:
                save_config.write(f)
            print(f"[CONFIG] Configuration saved to: {self.config_path}")
        except Exception as e:
            print(f"[CONFIG] Failed to save config: {e}")
    
    # Property getters for commonly used values
    @property
    def LOG_DIR(self) -> Path:
        path_str = self.config.get('General', 'log_dir', fallback='', raw=True)
        return self._sanitize_path(path_str)
    
    @property
    def LOG_PATTERN(self) -> re.Pattern:
        pattern = self.config.get('General', 'log_pattern', 
                                 fallback=r'WoWCombatLog-\d{6}_\d{6}\.txt$', raw=True)
        return re.compile(pattern)
    
    @property
    def RECORDING_EXTENSION(self) -> str:
        return self.config.get('General', 'recording_extension', fallback='.mp4', raw=True)
    
    @property
    def OBS_HOST(self) -> str:
        return self.config.get('OBS', 'host', fallback='localhost', raw=True)
    
    @property
    def OBS_PORT(self) -> int:
        return self.config.getint('OBS', 'port', fallback=4455)
    
    @property
    def OBS_PASSWORD(self) -> str:
        return self.config.get('OBS', 'password', fallback='', raw=True)
    
    @property
    def AUTO_RENAME(self) -> bool:
        return self.config.getboolean('Recording', 'auto_rename', fallback=True)
    
    @property
    def RENAME_DELAY(self) -> int:
        return self.config.getint('Recording', 'rename_delay', fallback=3)
    
    @property
    def MAX_RENAME_ATTEMPTS(self) -> int:
        return self.config.getint('Recording', 'max_rename_attempts', fallback=10)
    
    @property
    def RECORDING_PATH_FALLBACK(self) -> Optional[Path]:
        """Get fallback recording path from config"""
        path_str = self.config.get('Recording', 'recording_path_fallback', fallback='', raw=True)
        if path_str and path_str.strip():
            return self._sanitize_path(path_str.strip())
        return None
    
    @property
    def BOSS_NAME_OVERRIDES(self) -> Dict[int, str]:
        """Get boss name overrides from config"""
        overrides = {}
        if 'BossNames' in self.config:
            for key, value in self.config.items('BossNames', raw=True):
                try:
                    boss_id = int(key)
                    overrides[boss_id] = value
                except ValueError:
                    continue
        return overrides
    
    @property
    def MIN_RECORDING_DURATION(self) -> int:
        return self.config.getint('Recording', 'min_recording_duration', fallback=5)
    
    def set_boss_name_override(self, boss_id: int, name: str):
        """Set a boss name override"""
        if 'BossNames' not in self.config:
            self.config.add_section('BossNames')
        self.config.set('BossNames', str(boss_id), name)
        self.save_config()
    
    def get(self, section: str, key: str, fallback: Any = None) -> Any:
        """Generic getter for any configuration value"""
        try:
            return self.config.get(section, key, fallback=fallback, raw=True)
        except (configparser.NoSectionError, configparser.NoOptionError):
            return fallback
    
    def set(self, section: str, key: str, value: str):
        """Generic setter for configuration values"""
        if section not in self.config:
            self.config.add_section(section)
        self.config.set(section, key, value)
        self.save_config()
    
    def print_config(self):
        """Print current configuration (for debugging)"""
        print("\n=== Current Configuration ===")
        for section in self.config.sections():
            print(f"\n[{section}]")
            for key, value in self.config.items(section, raw=True):
                # Don't print passwords in plain text
                if 'password' in key.lower() and value:
                    print(f"  {key} = [HIDDEN]")
                else:
                    print(f"  {key} = {value}")
        print("=============================\n")
    
    def validate_config(self) -> Dict[str, List[str]]:
        """Validate configuration and return any errors"""
        errors = {}
        
        # Check log directory
        log_dir = self.LOG_DIR
        if not log_dir:
            if 'General' not in errors:
                errors['General'] = []
            errors['General'].append("Log directory is empty")
        elif not log_dir.exists():
            if 'General' not in errors:
                errors['General'] = []
            errors['General'].append(f"Log directory does not exist: {log_dir}")
        
        # Check OBS connection settings
        if not self.OBS_HOST:
            if 'OBS' not in errors:
                errors['OBS'] = []
            errors['OBS'].append("OBS host cannot be empty")
        
        # Check recording extension
        if not self.RECORDING_EXTENSION.startswith('.'):
            if 'General' not in errors:
                errors['General'] = []
            errors['General'].append(f"Recording extension must start with '.': {self.RECORDING_EXTENSION}")
        
        return errors