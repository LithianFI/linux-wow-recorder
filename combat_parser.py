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
        try:
            # Split timestamp (before double space) from CSV data
            ts_part, rest = self.raw_line.split("  ", 1)
            self.timestamp = ts_part.strip()
            
            # Parse CSV fields
            csv_reader = csv.reader(io.StringIO(rest))
            self.fields = next(csv_reader)
            if self.fields:
                self.event_type = self.fields[0].strip().upper()
                
        except (ValueError, StopIteration):
            # Not a valid combat log line format
            pass
    
    @property
    def is_encounter_start(self) -> bool:
        """Check if this is an ENCOUNTER_START event."""
        return self.event_type == "ENCOUNTER_START"
    
    @property
    def is_encounter_end(self) -> bool:
        """Check if this is an ENCOUNTER_END event."""
        return self.event_type == "ENCOUNTER_END"
    
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
    
    def is_valid(self) -> bool:
        """Check if this is a valid parsable event."""
        return bool(self.event_type)
    
    def __str__(self) -> str:
        return f"CombatEvent({self.event_type} at {self.timestamp})"


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
    
    def generate_filename(self, boss_info: BossInfo, file_time: datetime) -> str:
        """Generate a filename for a recording based on boss info."""
        # Get difficulty name
        difficulty_name = self._get_difficulty_name(boss_info.difficulty_id)
        
        # Format timestamp
        date_str = file_time.strftime("%Y-%m-%d")
        time_str = file_time.strftime("%H-%M-%S")
        
        # Create filename
        filename = f"{date_str}_{time_str}_{boss_info.formatted_name}_{difficulty_name}"
        filename += self.config.RECORDING_EXTENSION
        
        return filename
    
    def rename_recording(self, recording_path: Path, boss_info: BossInfo) -> Optional[Path]:
        """Rename a recording file with boss information."""
        try:
            # Generate new filename
            file_time = datetime.fromtimestamp(recording_path.stat().st_mtime)
            new_filename = self.generate_filename(boss_info, file_time)
            new_path = recording_path.parent / new_filename
            
            # Handle duplicates
            new_path = self._handle_duplicate_filename(new_path, boss_info, file_time)
            
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
            
            new_filename = self.generate_filename(boss_info_copy, file_time)
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
    
    def process_encounter_end(self, boss_info: BossInfo, recording_duration: float) -> bool:
        """Stop recording and handle the recording file."""
        print(f"[PROC] Stopping recording for: {boss_info.name}")
        
        # Stop OBS recording
        if not self.obs.stop_recording():
            print("[PROC] Failed to stop OBS recording")
            return False
        
        # Wait before file operations
        time.sleep(self.config.RENAME_DELAY)
        
        # Process the recording
        return self._process_recording_file(boss_info, recording_duration)
    
    def _process_recording_file(self, boss_info: BossInfo, duration: float) -> bool:
        """Process the recording file (rename or delete)."""
        # Check minimum duration
        if duration < self.config.MIN_RECORDING_DURATION:
            print(f"[PROC] Recording too short ({duration:.1f}s), will delete")
            return self._handle_short_recording(duration)
        
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
        new_path = self.file_manager.rename_recording(recording_path, boss_info)
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
    
    def process_line(self, line: str):
        """Process a single combat log line."""
        # Parse the line
        event = CombatEvent(line)
        if not event.is_valid():
            return
        
        # Handle the event
        if event.is_encounter_start:
            self._handle_encounter_start(event)
        elif event.is_encounter_end:
            self._handle_encounter_end(event)
    
    def _handle_encounter_start(self, event: CombatEvent):
        """Handle ENCOUNTER_START event."""
        # Don't start if already recording
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
        
        # Wait for active threads to complete (with timeout)
        for thread in self._active_threads:
            if thread.is_alive():
                thread.join(timeout=5.0)
        
        self._active_threads.clear()
        print("[PARSER] Shutdown complete")