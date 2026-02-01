"""
Recording file management operations.
"""

import time
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

from combat_parser.events import BossInfo, DungeonInfo


class RecordingFileManager:
    """Manages recording file operations: finding, renaming, deleting."""
    
    # Common video file extensions
    VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.flv', '.mov', '.ts', '.m3u8', '.avi', '.wmv'}
    
    def __init__(self, config, obs_client):
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
    
    def find_recording_by_name(self, filename: str) -> Optional[Path]:
        """Find a recording file by name."""
        record_dir = self.get_recording_directory()
        if not record_dir:
            return None
        
        file_path = record_dir / filename
        if file_path.exists() and file_path.is_file():
            return file_path
        
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