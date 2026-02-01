"""
Combat Log Parser for WoW Raid Recorder.
Parses WoW combat logs and triggers recording actions.
"""

import csv
import io
import time
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

from obs_client import OBSClient
from state_manager import RecordingState
from config_manager import ConfigManager


@dataclass
class BossInfo:
    """Information about a boss encounter."""
    boss_id: int
    name: str
    difficulty_id: int
    instance_id: int
    timestamp: str = ""
    
    @property
    def formatted_name(self) -> str:
        """Get boss name formatted for filename."""
        # Remove special characters and replace spaces
        cleaned = re.sub(r'[<>:"/\\|?*]', '', self.name)
        cleaned = cleaned.replace(" ", "_")
        cleaned = cleaned.replace("'", "")
        cleaned = cleaned.replace(",", "")
        return cleaned.strip()
    
@dataclass 
class DungeonInfo:
    """Information about a Mythic+ dungeon run."""
    dungeon_id: int
    name: str
    dungeon_level: int
    timestamp: str = ""
    
    @property
    def formatted_name(self) -> str:
        """Get dungeon name formatted for filename."""
        # Remove special characters and replace spaces
        cleaned = re.sub(r'[<>:"/\\|?*]', '', self.name)
        cleaned = cleaned.replace(" ", "_")
        cleaned = cleaned.replace("'", "")
        cleaned = cleaned.replace(",", "")
        cleaned = cleaned.replace(":", "")
        cleaned = cleaned.replace("-", "_")
        return cleaned.strip()


class CombatEvent:
    """Represents a parsed combat log event."""
    
    def __init__(self, raw_line: str):
        self.raw_line = raw_line
        self.timestamp = ""
        self.event_type = ""
        self.fields: List[str] = []
        self._parse_line()
    
    def _parse_line(self):
        """Parse the raw log line into components."""
        line = self.raw_line.strip()
        if not line:
            return
        
        try:
            # Find the first double space to separate timestamp from data
            if "  " in line:
                ts_end = line.find("  ")
                self.timestamp = line[:ts_end].strip()
                data = line[ts_end + 2:].strip()
            else:
                # Fallback: split on first space after time
                first_space = line.find(" ")
                if first_space == -1:
                    return
                
                second_space = line.find(" ", first_space + 1)
                if second_space == -1:
                    return
                
                self.timestamp = line[:second_space].strip()
                data = line[second_space + 1:].strip()
            
            # Parse CSV data with proper quote handling
            csv_reader = csv.reader(io.StringIO(data), quotechar='"', delimiter=',')
            
            try:
                row = next(csv_reader)
                # Clean up fields
                self.fields = []
                for field in row:
                    field = field.strip()
                    # Remove surrounding quotes if present
                    if field.startswith('"') and field.endswith('"'):
                        field = field[1:-1]
                    self.fields.append(field)
                
                if self.fields:
                    self.event_type = self.fields[0].upper()
                    
            except StopIteration:
                pass
                
        except Exception as e:
            if "ENCOUNTER" in line or "CHALLENGE_MODE" in line:
                print(f"[PARSER] Parse error: {e}")
                print(f"[PARSER] Line: {line[:100]}...")
    
    @property
    def is_encounter_start(self) -> bool:
        """Check if this is an ENCOUNTER_START event."""
        return self.event_type == "ENCOUNTER_START"
    
    @property
    def is_encounter_end(self) -> bool:
        """Check if this is an ENCOUNTER_END event."""
        return self.event_type == "ENCOUNTER_END"
    
    @property
    def is_dungeon_start(self) -> bool:
        """Check if this is a CHALLENGE_MODE_START event."""
        return self.event_type == "CHALLENGE_MODE_START"
    
    @property
    def is_dungeon_end(self) -> bool:
        """Check if this is a CHALLENGE_MODE_END event."""
        return self.event_type == "CHALLENGE_MODE_END"
    
    @property
    def is_zone_change(self) -> bool:
        """Check if this is a ZONE_CHANGE event."""
        return self.event_type == "ZONE_CHANGE"
    
    def get_boss_info(self) -> Optional[BossInfo]:
        """Extract boss information from ENCOUNTER_START event."""
        if not self.is_encounter_start or len(self.fields) < 6:
            return None
        
        try:
            return BossInfo(
                boss_id=int(self.fields[1]),
                name=self.fields[2],
                difficulty_id=int(self.fields[3]),
                instance_id=int(self.fields[5]),
                timestamp=self.timestamp
            )
        except (ValueError, IndexError):
            return None
    
    def get_dungeon_info(self) -> Optional[DungeonInfo]:
        """Extract dungeon information from CHALLENGE_MODE_START event."""
        if not self.is_dungeon_start:
            return None
        
        print(f"[PARSER] Parsing dungeon start with {len(self.fields)} fields: {self.fields}")
        
        if len(self.fields) < 5:
            print(f"[PARSER] Not enough fields for dungeon: {self.fields}")
            return None
        
        try:
            # CHALLENGE_MODE_START,"Tazavesh, the Veiled Market",2441,391,14,[10,9,147]
            # Fields: [0]=CHALLENGE_MODE_START, [1]=zoneName, [2]=instanceID, [3]=challengeModeID, [4]=keystoneLevel
            
            dungeon_name = self.fields[1]
            instance_id = int(self.fields[2])
            keystone_level = int(self.fields[4])
            
            print(f"[PARSER] Parsed dungeon: {dungeon_name} (ID: {instance_id}) +{keystone_level}")
            
            return DungeonInfo(
                dungeon_id=instance_id,
                name=dungeon_name,
                dungeon_level=keystone_level,
                timestamp=self.timestamp
            )
            
        except (ValueError, IndexError) as e:
            print(f"[PARSER] Error parsing dungeon info: {e}")
            print(f"[PARSER] Fields were: {self.fields}")
            return None
    
    def is_valid(self) -> bool:
        """Check if this is a valid parsable event."""
        return bool(self.event_type) and len(self.fields) > 0
    
    def __str__(self) -> str:
        return f"CombatEvent({self.event_type} at {self.timestamp[:19]})"


class RecordingFileManager:
    """Manages recording file operations: finding, renaming, deleting."""
    
    # Common video file extensions
    VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.flv', '.mov', '.ts', '.m3u8', '.avi', '.wmv'}
    
    def __init__(self, config: ConfigManager, obs_client: OBSClient):
        self.config = config
        self.obs = obs_client
        self.last_renamed_path: Optional[Path] = None
    
    def get_recording_directory(self) -> Optional[Path]:
        """Get the current recording directory from OBS or config fallback."""
        try:
            # Try to get from OBS first
            settings = self.obs.get_recording_settings()
            if settings and 'record_directory' in settings:
                path = Path(settings['record_directory'])
                if path.exists():
                    print(f"[FILE] Using OBS recording directory: {path}")
                    return path
            
            # Fallback to config
            fallback = self.config.RECORDING_PATH_FALLBACK
            if fallback:
                print(f"[FILE] Using fallback directory: {fallback}")
                if not fallback.exists():
                    fallback.mkdir(parents=True, exist_ok=True)
                return fallback
            
            print("[FILE] No recording directory available")
            return None
            
        except Exception as e:
            print(f"[FILE] Error getting recording directory: {e}")
            return None
    
    def find_latest_recording(self) -> Optional[Path]:
        """Find the most recent recording file in recording directory."""
        record_dir = self.get_recording_directory()
        if not record_dir:
            return None
        
        try:
            # Find all video files
            video_files = []
            for file in record_dir.iterdir():
                if file.suffix.lower() in self.VIDEO_EXTENSIONS and file.is_file():
                    video_files.append(file)
            
            if not video_files:
                print(f"[FILE] No video files found in {record_dir}")
                return None
            
            # Get most recently modified file
            latest = max(video_files, key=lambda f: f.stat().st_mtime)
            print(f"[FILE] Found latest recording: {latest.name}")
            return latest
            
        except Exception as e:
            print(f"[FILE] Error finding recordings: {e}")
            return None
    
    def validate_file_stable(self, file_path: Path, check_interval: float = 1.0) -> bool:
        """Check if a file has stopped changing (OBS finished writing)."""
        try:
            if not file_path.exists():
                return False
            
            # Check file size stability
            initial_size = file_path.stat().st_size
            time.sleep(check_interval)
            final_size = file_path.stat().st_size
            
            if initial_size != final_size:
                print(f"[FILE] File still changing: {initial_size} → {final_size} bytes")
                return False
            
            return True
            
        except Exception as e:
            print(f"[FILE] Error validating file stability: {e}")
            return False
    
    def generate_filename(self, boss_info: BossInfo = None, dungeon_info: DungeonInfo = None, 
                         file_time: datetime = None) -> str:
        """Generate a filename for a recording based on encounter info."""
        # Determine if this is a boss or dungeon
        if boss_info:
            # Get difficulty name
            difficulty_name = self._get_difficulty_name(boss_info.difficulty_id)
            
            # Format timestamp
            if not file_time:
                file_time = datetime.now()
            date_str = file_time.strftime("%Y-%m-%d")
            time_str = file_time.strftime("%H-%M-%S")
            
            # Create filename
            filename = f"{date_str}_{time_str}_{boss_info.formatted_name}_{difficulty_name}"
        
        elif dungeon_info:
            # Format timestamp
            if not file_time:
                file_time = datetime.now()
            date_str = file_time.strftime("%Y-%m-%d")
            time_str = file_time.strftime("%H-%M-%S")
            
            # Create M+ filename
            filename = f"{date_str}_{time_str}_{dungeon_info.formatted_name}_M+{dungeon_info.dungeon_level}"
        
        else:
            # Fallback generic name
            if not file_time:
                file_time = datetime.now()
            date_str = file_time.strftime("%Y-%m-%d")
            time_str = file_time.strftime("%H-%M-%S")
            filename = f"{date_str}_{time_str}_Recording"
        
        filename += self.config.RECORDING_EXTENSION
        
        return filename
    
    def rename_recording(self, recording_path: Path, boss_info: BossInfo = None, 
                        dungeon_info: DungeonInfo = None) -> Optional[Path]:
        """Rename a recording file with encounter information."""
        try:
            # Generate new filename
            file_time = datetime.fromtimestamp(recording_path.stat().st_mtime)
            
            if boss_info:
                new_filename = self.generate_filename(boss_info=boss_info, file_time=file_time)
            elif dungeon_info:
                new_filename = self.generate_filename(dungeon_info=dungeon_info, file_time=file_time)
            else:
                new_filename = self.generate_filename(file_time=file_time)
            
            new_path = recording_path.parent / new_filename
            
            # Handle duplicates
            if boss_info:
                new_path = self._handle_duplicate_filename(new_path, boss_info, file_time)
            elif dungeon_info:
                new_path = self._handle_duplicate_dungeon_filename(new_path, dungeon_info, file_time)
            else:
                new_path = self._handle_duplicate_generic_filename(new_path, file_time)
            
            # Perform rename
            recording_path.rename(new_path)
            print(f"[FILE] Renamed to: {new_path.name}")
            
            self.last_renamed_path = new_path
            return new_path
            
        except Exception as e:
            print(f"[FILE] Error renaming recording: {e}")
            return None
    
    def delete_recording(self, recording_path: Path, reason: str = "") -> bool:
        """Delete a recording file."""
        try:
            if not recording_path.exists():
                print(f"[FILE] File already doesn't exist: {recording_path}")
                return False
            
            # Get file info before deletion
            file_size = recording_path.stat().st_size
            file_size_mb = file_size / (1024 * 1024)
            
            # Delete the file
            recording_path.unlink()
            
            reason_text = f" ({reason})" if reason else ""
            print(f"[FILE] Deleted recording{reason_text}: {recording_path.name} ({file_size_mb:.2f}MB)")
            return True
            
        except Exception as e:
            print(f"[FILE] Error deleting recording: {e}")
            return False
    
    def _get_difficulty_name(self, difficulty_id: int) -> str:
        """Convert difficulty ID to readable name."""
        difficulties = {
            1: "Normal", 2: "Heroic", 3: "Mythic", 4: "Mythic+",
            5: "Timewalking", 7: "LFR", 9: "40Player",
            14: "Normal", 15: "Heroic", 16: "Mythic", 17: "LFR",
            23: "Mythic", 24: "Timewalking", 33: "Timewalking",
        }
        return difficulties.get(difficulty_id, f"Difficulty_{difficulty_id}")
    
    def _handle_duplicate_filename(self, path: Path, boss_info: BossInfo, 
                                 file_time: datetime) -> Path:
        """Handle duplicate filenames by adding attempt counters."""
        counter = 1
        original_path = path
        
        while path.exists() and counter <= self.config.MAX_RENAME_ATTEMPTS:
            # Create boss name with attempt counter
            boss_with_counter = f"{boss_info.name}_attempt{counter}"
            boss_info_copy = BossInfo(
                boss_id=boss_info.boss_id,
                name=boss_with_counter,
                difficulty_id=boss_info.difficulty_id,
                instance_id=boss_info.instance_id,
                timestamp=boss_info.timestamp
            )
            
            new_filename = self.generate_filename(boss_info=boss_info_copy, file_time=file_time)
            path = original_path.parent / new_filename
            counter += 1
        
        if path.exists():
            print(f"[FILE] Max rename attempts reached, keeping: {original_path.name}")
            return original_path
        
        return path
    
    def _handle_duplicate_dungeon_filename(self, path: Path, dungeon_info: DungeonInfo,
                                         file_time: datetime) -> Path:
        """Handle duplicate dungeon filenames by adding attempt counters."""
        counter = 1
        original_path = path
        
        while path.exists() and counter <= self.config.MAX_RENAME_ATTEMPTS:
            # Create dungeon name with attempt counter
            dungeon_with_counter = f"{dungeon_info.name}_attempt{counter}"
            dungeon_info_copy = DungeonInfo(
                dungeon_id=dungeon_info.dungeon_id,
                name=dungeon_with_counter,
                dungeon_level=dungeon_info.dungeon_level,
                timestamp=dungeon_info.timestamp
            )
            
            new_filename = self.generate_filename(dungeon_info=dungeon_info_copy, file_time=file_time)
            path = original_path.parent / new_filename
            counter += 1
        
        if path.exists():
            print(f"[FILE] Max rename attempts reached, keeping: {original_path.name}")
            return original_path
        
        return path
    
    def _handle_duplicate_generic_filename(self, path: Path, file_time: datetime) -> Path:
        """Handle duplicate generic filenames."""
        counter = 1
        original_path = path
        
        while path.exists() and counter <= self.config.MAX_RENAME_ATTEMPTS:
            new_filename = f"{file_time.strftime('%Y-%m-%d_%H-%M-%S')}_Recording_{counter}{self.config.RECORDING_EXTENSION}"
            path = original_path.parent / new_filename
            counter += 1
        
        if path.exists():
            print(f"[FILE] Max rename attempts reached, keeping: {original_path.name}")
            return original_path
        
        return path


class RecordingProcessor:
    """Processes recordings based on encounter events."""
    
    def __init__(self, obs_client: OBSClient, file_manager: RecordingFileManager,
                 config: ConfigManager):
        self.obs = obs_client
        self.file_manager = file_manager
        self.config = config
    
    def process_encounter_start(self, boss_info: BossInfo) -> bool:
        """Start recording for an encounter."""
        # Check if difficulty is enabled
        if not self.config.is_difficulty_enabled(boss_info.difficulty_id):
            diff_name = self.file_manager._get_difficulty_name(boss_info.difficulty_id)
            print(f"[PROC] Skipping {diff_name} encounter - not enabled in config")
            return False
        
        print(f"[PROC] Starting recording for: {boss_info.name}")
        
        # Start OBS recording
        if not self.obs.start_recording():
            print("[PROC] Failed to start OBS recording")
            return False
        
        return True
    
    def process_dungeon_start(self, dungeon_info: DungeonInfo) -> bool:
        """Start recording for a Mythic+ dungeon."""
        # Check if M+ is enabled
        if not self.config.RECORD_MPLUS:
            print(f"[PROC] Skipping M+ dungeon - not enabled in config")
            return False
        
        print(f"[PROC] Starting recording for: {dungeon_info.name} (+{dungeon_info.dungeon_level})")
        
        # Start OBS recording
        if not self.obs.start_recording():
            print("[PROC] Failed to start OBS recording")
            return False
        
        return True
    
    def process_encounter_end(self, boss_info: BossInfo, recording_duration: float) -> bool:
        """Stop recording and handle the recording file."""
        if not self.config.is_difficulty_enabled(boss_info.difficulty_id):
            diff_name = self.file_manager._get_difficulty_name(boss_info.difficulty_id)
            return False
        
        print(f"[PROC] Stopping recording for: {boss_info.name}")
        
        # Stop OBS recording
        if not self.obs.stop_recording():
            print("[PROC] Failed to stop OBS recording")
            return False
        
        # Wait before file operations
        time.sleep(self.config.RENAME_DELAY)
        
        # Process the recording
        return self._process_recording_file(boss_info=boss_info, recording_duration=recording_duration)
    
    def process_dungeon_end(self, dungeon_info: DungeonInfo = None, recording_duration: float = 0, 
                           reason: str = "") -> bool:
        """Stop recording and handle the recording file for dungeon."""
        if not self.config.RECORD_MPLUS:
            return False
        
        print(f"[PROC] Stopping dungeon recording{f' ({reason})' if reason else ''}")
        
        # Stop OBS recording
        if not self.obs.stop_recording():
            print("[PROC] Failed to stop OBS recording")
            return False
        
        # Wait before file operations
        time.sleep(self.config.RENAME_DELAY)
        
        # Process the recording
        return self._process_recording_file(dungeon_info=dungeon_info, recording_duration=recording_duration)
    
    def _process_recording_file(self, boss_info: BossInfo = None, dungeon_info: DungeonInfo = None,
                               recording_duration: float = 0) -> bool:
        """Process the recording file (rename or delete)."""
        # Check minimum duration
        if recording_duration < self.config.MIN_RECORDING_DURATION:
            print(f"[PROC] Recording too short ({recording_duration:.1f}s), will delete")
            return self._handle_short_recording(recording_duration)
        
        # Get the recording file
        recording_path = self.file_manager.find_latest_recording()
        if not recording_path:
            print("[PROC] Could not find recording file")
            return False
        
        # Validate file is stable
        if not self.file_manager.validate_file_stable(recording_path):
            print("[PROC] Recording file not stable, skipping")
            return False
        
        # Rename the file
        if boss_info:
            new_path = self.file_manager.rename_recording(recording_path, boss_info=boss_info)
        elif dungeon_info:
            new_path = self.file_manager.rename_recording(recording_path, dungeon_info=dungeon_info)
        else:
            new_path = self.file_manager.rename_recording(recording_path)
        
        return new_path is not None
    
    def _handle_short_recording(self, duration: float) -> bool:
        """Handle a recording that's too short."""
        if not self.config.DELETE_SHORT_RECORDINGS:
            print(f"[PROC] Short recording kept (delete_short_recordings = false)")
            return True
        
        # Find and delete the short recording
        recording_path = self.file_manager.find_latest_recording()
        if recording_path:
            reason = f"too short ({duration:.1f}s)"
            return self.file_manager.delete_recording(recording_path, reason)
        
        return False


class CombatParser:
    """Main parser that coordinates combat log parsing and recording actions."""

    def __init__(self, obs_client: OBSClient, state_manager: RecordingState,
                 config_manager: ConfigManager):
        self.obs = obs_client
        self.state = state_manager
        self.config = config_manager

        # Initialize components
        self.file_manager = RecordingFileManager(config_manager, obs_client)
        self.processor = RecordingProcessor(obs_client, self.file_manager, config_manager)

        # Thread management
        self._active_threads: List[threading.Thread] = []
        self._cleanup_completed_threads()

        # Event callbacks for frontend
        self.on_event: Optional[callable] = None
        self.on_recording_saved: Optional[callable] = None
        
        # Dungeon timeout monitoring
        self._dungeon_monitor_thread = None
        self._dungeon_monitor_running = False
        self._start_dungeon_monitor()
    
    def process_line(self, line: str):
        """Process a single combat log line."""
        # Parse the line
        event = CombatEvent(line)
        if not event.is_valid():
            return
        
        # Update activity timestamp for dungeon idle detection
        if self.state.dungeon_active:
            self.state.update_activity()
        
        # Handle the event - prioritize dungeons over encounters
        if event.is_dungeon_start:
            self._handle_dungeon_start(event)
        elif event.is_dungeon_end:
            self._handle_dungeon_end(event, "dungeon_complete")
        elif event.is_zone_change:
            self._handle_zone_change(event)
        elif event.is_encounter_start:
            self._handle_encounter_start(event)
        elif event.is_encounter_end:
            self._handle_encounter_end(event)
    
    def _handle_dungeon_start(self, event: CombatEvent):
        """Handle CHALLENGE_MODE_START event."""
        # Don't start if already recording a dungeon
        if self.state.dungeon_active:
            return
        
        # Extract dungeon information
        dungeon_info = event.get_dungeon_info()
        if not dungeon_info:
            print(f"[PARSER] Could not parse dungeon info from: {event}")
            return
        
        # Start the dungeon in state
        self.state.start_dungeon(
            dungeon_info.dungeon_id,
            dungeon_info.name,
            dungeon_info.dungeon_level,
            dungeon_info.timestamp
        )
        
        # Start recording in background thread
        thread = threading.Thread(
            target=self._process_dungeon_start_thread,
            args=(dungeon_info,),
            daemon=True
        )
        thread.start()
        self._active_threads.append(thread)

        # Emit event for frontend
        if self.on_event:
            self.on_event({
                'type': 'DUNGEON_START',
                'timestamp': dungeon_info.timestamp,
                'dungeon_name': dungeon_info.name,
                'dungeon_level': dungeon_info.dungeon_level,
                'dungeon_id': dungeon_info.dungeon_id,
            })

        print(f"[PARSER] Started M+ dungeon: {dungeon_info.name} (+{dungeon_info.dungeon_level})")
    
    def _handle_dungeon_end(self, event: CombatEvent, reason: str = "dungeon_complete"):
        """Handle CHALLENGE_MODE_END event."""
        # Only process if we're in an active dungeon
        if not self.state.dungeon_active:
            return

        # Get dungeon info and recording duration
        dungeon_name = self.state.dungeon_name
        dungeon_level = self.state.dungeon_level
        dungeon_duration = self.state.get_encounter_duration()

        # Check success status from event fields
        # CHALLENGE_MODE_END format: instanceID, zoneName, challengeModeID, success
        is_success = False
        try:
            if len(event.fields) >= 5:
                is_success = event.fields[4] == "1"
        except (IndexError, ValueError):
            pass

        # Create dungeon info for processing
        dungeon_info = DungeonInfo(
            dungeon_id=self.state.dungeon_id or 0,
            name=dungeon_name or "Unknown Dungeon",
            dungeon_level=dungeon_level or 0,
            timestamp=event.timestamp
        )

        # Emit event for frontend
        if self.on_event:
            self.on_event({
                'type': 'DUNGEON_END',
                'timestamp': event.timestamp,
                'dungeon_name': dungeon_name,
                'dungeon_level': dungeon_level,
                'duration': round(dungeon_duration, 1),
                'is_success': is_success,
                'reason': reason,
            })

        # Wait a moment before processing
        time.sleep(3)

        # Process dungeon end in background thread
        thread = threading.Thread(
            target=self._process_dungeon_end_thread,
            args=(dungeon_info, dungeon_duration, reason),
            daemon=True
        )
        thread.start()
        self._active_threads.append(thread)

        # Reset state
        self.state.reset()

        print(f"[PARSER] Ended M+ dungeon: {dungeon_info.name} ({reason})")
    
    def _handle_zone_change(self, event: CombatEvent):
        """Handle ZONE_CHANGE event during dungeon runs."""
        # Only process if we're in an active dungeon
        if not self.state.dungeon_active:
            return
        
        print(f"[PARSER] Zone change detected during dungeon run")
        
        # Check if we changed to a different instance (likely left dungeon)
        try:
            # ZONE_CHANGE format: uiMapID, zoneName
            # If zoneName changes significantly, assume dungeon ended
            if len(event.fields) >= 3:
                new_zone = event.fields[2]
                current_dungeon = self.state.dungeon_name
                
                # Simple check: if zone doesn't contain dungeon name (case-insensitive)
                if current_dungeon and current_dungeon.lower() not in new_zone.lower():
                    print(f"[PARSER] Zone changed from dungeon to: {new_zone}")
                    self._handle_dungeon_end(event, "zone_change")
        except (IndexError, ValueError):
            pass
    
    def _handle_dungeon_timeout(self):
        """Handle dungeon timeout due to inactivity."""
        if not self.state.dungeon_active:
            return
        
        # Create a synthetic event for timeout
        synthetic_event = CombatEvent(f"{datetime.now().strftime('%H:%M:%S')}  CHALLENGE_MODE_END,{self.state.dungeon_id},{self.state.dungeon_name},0,0")
        self._handle_dungeon_end(synthetic_event, "timeout")
    
    def _handle_encounter_start(self, event: CombatEvent):
        """Handle ENCOUNTER_START event."""
        # Don't start if already recording (either encounter or dungeon)
        if self.state.is_recording:
            return
        
        # Extract boss information
        boss_info = event.get_boss_info()
        if not boss_info:
            print(f"[PARSER] Could not parse boss info from: {event}")
            return
        
        # Apply boss name overrides
        overrides = self.config.BOSS_NAME_OVERRIDES
        if boss_info.boss_id in overrides:
            boss_info.name = overrides[boss_info.boss_id]
        
        # Start the encounter in state
        self.state.start_encounter(
            boss_info.boss_id, boss_info.name,
            boss_info.difficulty_id, boss_info.instance_id
        )
        
        # Start recording in background thread
        thread = threading.Thread(
            target=self._process_encounter_start_thread,
            args=(boss_info,),
            daemon=True
        )
        thread.start()
        self._active_threads.append(thread)

        # Emit event for frontend
        if self.on_event:
            self.on_event({
                'type': 'ENCOUNTER_START',
                'timestamp': boss_info.timestamp,
                'boss_name': boss_info.name,
                'difficulty_id': boss_info.difficulty_id,
            })

        print(f"[PARSER] Started encounter: {boss_info.name}")
    
    def _handle_encounter_end(self, event: CombatEvent):
        """Handle ENCOUNTER_END event."""
        # Only process if we're in an active encounter
        if not self.state.encounter_active:
            return

        # Get boss info and recording duration
        boss_name = self.state.boss_name
        difficulty_id = self.state.difficulty_id
        encounter_duration = self.state.get_encounter_duration()

        # Check kill/wipe status from event fields
        # ENCOUNTER_END format: encounterID, encounterName, difficultyID, groupSize, success
        is_kill = False
        try:
            if len(event.fields) >= 6:
                is_kill = event.fields[5] == "1"
        except (IndexError, ValueError):
            pass

        # Create boss info for processing
        boss_info = BossInfo(
            boss_id=self.state.boss_id or 0,
            name=boss_name or "Unknown",
            difficulty_id=difficulty_id or 0,
            instance_id=self.state.instance_id or 0,
            timestamp=event.timestamp
        )

        # Emit event for frontend
        if self.on_event:
            self.on_event({
                'type': 'ENCOUNTER_END',
                'timestamp': event.timestamp,
                'boss_name': boss_name,
                'difficulty_id': difficulty_id,
                'duration': round(encounter_duration, 1),
                'is_kill': is_kill,
            })

        # Wait for encounter to fully end
        time.sleep(3)

        # Process encounter end in background thread
        thread = threading.Thread(
            target=self._process_encounter_end_thread,
            args=(boss_info, encounter_duration),
            daemon=True
        )
        thread.start()
        self._active_threads.append(thread)

        # Reset state
        self.state.reset()

        print(f"[PARSER] Ended encounter: {boss_info.name}")
    
    def _start_dungeon_monitor(self):
        """Start dungeon timeout monitoring thread."""
        self._dungeon_monitor_running = True
        self._dungeon_monitor_thread = threading.Thread(
            target=self._dungeon_monitor_loop,
            daemon=True
        )
        self._dungeon_monitor_thread.start()
        print("[PARSER] Started dungeon timeout monitor")
    
    def _dungeon_monitor_loop(self):
        """Monitor dungeon for inactivity timeout."""
        while self._dungeon_monitor_running:
            try:
                if self.state.dungeon_active:
                    timeout = self.config.DUNGEON_TIMEOUT_SECONDS
                    if self.state.is_dungeon_idle(timeout):
                        print(f"[PARSER] Dungeon idle for {timeout}s, triggering timeout")
                        self._handle_dungeon_timeout()
                
                time.sleep(5)  # Check every 5 seconds
            except Exception as e:
                print(f"[PARSER] Error in dungeon monitor: {e}")
                time.sleep(5)
    
    def _process_dungeon_start_thread(self, dungeon_info: DungeonInfo):
        """Thread function for starting dungeon recording."""
        success = self.processor.process_dungeon_start(dungeon_info)
        if success:
            self.state.start_recording()
    
    def _process_dungeon_end_thread(self, dungeon_info: DungeonInfo, duration: float, reason: str):
        """Thread function for ending dungeon recording."""
        self.processor.process_dungeon_end(dungeon_info, duration, reason)

        # Notify frontend that recording was saved/processed
        if self.on_recording_saved:
            self.on_recording_saved()
    
    def _process_encounter_start_thread(self, boss_info: BossInfo):
        """Thread function for starting encounter recording."""
        success = self.processor.process_encounter_start(boss_info)
        if success:
            self.state.start_recording()
    
    def _process_encounter_end_thread(self, boss_info: BossInfo, duration: float):
        """Thread function for ending encounter recording."""
        self.processor.process_encounter_end(boss_info, duration)

        # Notify frontend that recording was saved/processed
        if self.on_recording_saved:
            self.on_recording_saved()
    
    def _cleanup_completed_threads(self):
        """Remove completed threads from active list."""
        self._active_threads = [t for t in self._active_threads if t.is_alive()]
    
    def shutdown(self):
        """Clean shutdown of the parser."""
        print("[PARSER] Shutting down...")
        
        # Stop dungeon monitor
        self._dungeon_monitor_running = False
        if self._dungeon_monitor_thread and self._dungeon_monitor_thread.is_alive():
            self._dungeon_monitor_thread.join(timeout=2.0)
        
        # Wait for active threads to complete (with timeout)
        for thread in self._active_threads:
            if thread.is_alive():
                thread.join(timeout=5.0)
        
        self._active_threads.clear()
        print("[PARSER] Shutdown complete")