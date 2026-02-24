"""
Recording processing logic for encounters and dungeons.
"""

import time
from typing import Optional
from pathlib import Path
from datetime import datetime

from constants import (
    DEFAULT_RENAME_DELAY,
    DEFAULT_MIN_RECORDING_DURATION,
    LOG_PREFIXES,
)

from combat_parser.events import BossInfo, DungeonInfo
from metadata_generator import RecordingMetadata


class RecordingProcessor:
    """Processes recordings based on encounter events."""
    
    def __init__(self, obs_client, file_manager, config):
        self.obs = obs_client
        self.file_manager = file_manager
        self.config = config
    
    def process_encounter_start(self, boss_info: BossInfo) -> bool:
        """Start recording for an encounter."""
        # Check if difficulty is enabled
        if not self.config.is_difficulty_enabled(boss_info.difficulty_id):
            diff_name = self.file_manager._get_difficulty_name(boss_info.difficulty_id)
            print(f"{LOG_PREFIXES['PROC']} Skipping {diff_name} encounter - not enabled in config")
            return False
        
        print(f"{LOG_PREFIXES['PROC']} Starting recording for: {boss_info.name}")
        
        # Start OBS recording
        if not self.obs.start_recording():
            print(f"{LOG_PREFIXES['PROC']} Failed to start OBS recording")
            return False
        
        return True
    
    def process_dungeon_start(self, dungeon_info: DungeonInfo) -> bool:
        """Start recording for a Mythic+ dungeon."""
        # Check if M+ is enabled
        if not self.config.RECORD_MPLUS:
            print(f"{LOG_PREFIXES['PROC']} Skipping M+ dungeon - not enabled in config")
            return False
        
        print(f"{LOG_PREFIXES['PROC']} Starting recording for: {dungeon_info.name} (+{dungeon_info.dungeon_level})")
        
        # Start OBS recording
        if not self.obs.start_recording():
            print(f"{LOG_PREFIXES['PROC']} Failed to start OBS recording")
            return False
        
        return True
    
    def process_encounter_end(self, boss_info: BossInfo, recording_duration: float,
                            metadata: Optional[RecordingMetadata] = None,
                            start_time: Optional[datetime] = None) -> bool:
        """Stop recording and handle the recording file."""
        if not self.config.is_difficulty_enabled(boss_info.difficulty_id):
            diff_name = self.file_manager._get_difficulty_name(boss_info.difficulty_id)
            return False
        
        print(f"{LOG_PREFIXES['PROC']} Stopping recording for: {boss_info.name}")
        
        # Stop OBS recording
        if not self.obs.stop_recording():
            print(f"{LOG_PREFIXES['PROC']} Failed to stop OBS recording")
            return False
        
        # Wait before file operations
        wait_time = self.config.RENAME_DELAY if hasattr(self.config, 'RENAME_DELAY') else DEFAULT_RENAME_DELAY
        time.sleep(wait_time)
        
        # Process the recording
        return self._process_recording_file(
            boss_info=boss_info, 
            recording_duration=recording_duration,
            metadata=metadata,
            start_time=start_time
        )
    
    def process_dungeon_end(self, dungeon_info: DungeonInfo = None, recording_duration: float = 0, 
                           reason: str = "", metadata: Optional[RecordingMetadata] = None,
                           start_time: Optional[datetime] = None) -> bool:
        """Stop recording and handle the recording file for dungeon."""
        if not self.config.RECORD_MPLUS:
            return False
        
        print(f"{LOG_PREFIXES['PROC']} Stopping dungeon recording{f' ({reason})' if reason else ''}")
        
        # Stop OBS recording
        if not self.obs.stop_recording():
            print(f"{LOG_PREFIXES['PROC']} Failed to stop OBS recording")
            return False
        
        # Wait before file operations
        wait_time = self.config.RENAME_DELAY if hasattr(self.config, 'RENAME_DELAY') else DEFAULT_RENAME_DELAY
        time.sleep(wait_time)
        
        # Process the recording
        return self._process_recording_file(
            dungeon_info=dungeon_info, 
            recording_duration=recording_duration,
            metadata=metadata,
            start_time=start_time
        )
    
    def _process_recording_file(self, boss_info: BossInfo = None, dungeon_info: DungeonInfo = None,
                               recording_duration: float = 0, metadata: Optional[RecordingMetadata] = None,
                               start_time: Optional[datetime] = None) -> bool:
        """Process the recording file (rename or delete)."""
        # Check minimum duration
        min_duration = self.config.MIN_RECORDING_DURATION if hasattr(self.config, 'MIN_RECORDING_DURATION') else DEFAULT_MIN_RECORDING_DURATION
        if recording_duration < min_duration:
            print(f"{LOG_PREFIXES['PROC']} Recording too short ({recording_duration:.1f}s), will delete")
            return self._handle_short_recording(recording_duration)
        
        # Get the recording file
        recording_path = self.file_manager.find_latest_recording()
        if not recording_path:
            print(f"{LOG_PREFIXES['PROC']} Could not find recording file")
            return False
        
        # Validate file is stable
        if not self.file_manager.validate_file_stable(recording_path):
            print(f"{LOG_PREFIXES['PROC']} Recording file not stable, skipping")
            return False
        
        # Determine start time for filename
        if start_time is None:
            start_time = datetime.fromtimestamp(recording_path.stat().st_mtime)
        
        # Rename the file based on naming scheme
        naming_scheme = self.config.FILE_NAMING_SCHEME if hasattr(self.config, 'FILE_NAMING_SCHEME') else 'simple'
        
        if naming_scheme == 'wcr' and metadata:
            # Use WCR-style naming
            new_path = self._rename_wcr_style(recording_path, metadata, start_time)
        else:
            # Use simple naming (existing behavior)
            if boss_info:
                new_path = self.file_manager.rename_recording(recording_path, boss_info=boss_info)
            elif dungeon_info:
                new_path = self.file_manager.rename_recording(recording_path, dungeon_info=dungeon_info)
            else:
                new_path = self.file_manager.rename_recording(recording_path)
        
        # Generate metadata JSON if enabled
        if new_path and metadata and self.config.GENERATE_METADATA_JSON:
            self._save_metadata_json(new_path, metadata)
        
        # ── Move into date subfolder ──────────────────────────────
        if new_path and getattr(self.config, 'ORGANIZE_BY_DATE', False):
            organised = self.file_manager.organize_into_date_subfolder(new_path)
            if organised:
                new_path = organised
        
        return new_path is not None
    
    def _rename_wcr_style(self, recording_path: Path, metadata: RecordingMetadata, 
                         start_time: datetime) -> Optional[Path]:
        """Rename recording using WCR-style filename."""
        try:
            extension = recording_path.suffix
            new_filename = metadata.generate_filename(start_time, extension)
            new_path = recording_path.parent / new_filename
            
            # Rename the file
            recording_path.rename(new_path)
            print(f"{LOG_PREFIXES['PROC']} ✅ Renamed (WCR): {recording_path.name} -> {new_filename}")
            
            return new_path
        except Exception as e:
            print(f"{LOG_PREFIXES['PROC']} ❌ Failed to rename with WCR style: {e}")
            return None
    
    def _save_metadata_json(self, video_path: Path, metadata: RecordingMetadata) -> bool:
        """Save metadata JSON file alongside the video."""
        try:
            json_path = video_path.with_suffix('.json')
            success = metadata.save_json(json_path)
            
            if success:
                print(f"{LOG_PREFIXES['PROC']} ✅ Saved metadata JSON: {json_path.name}")
            
            return success
        except Exception as e:
            print(f"{LOG_PREFIXES['PROC']} ❌ Failed to save metadata JSON: {e}")
            return False
    
    def _handle_short_recording(self, duration: float) -> bool:
        """Handle a recording that's too short."""
        if not self.config.DELETE_SHORT_RECORDINGS:
            print(f"{LOG_PREFIXES['PROC']} Short recording kept (delete_short_recordings = false)")
            return True
        
        # Find and delete the short recording
        recording_path = self.file_manager.find_latest_recording()
        if recording_path:
            reason = f"too short ({duration:.1f}s)"
            return self.file_manager.delete_recording(recording_path, reason)
        
        return False
    
    def force_stop_recording(self) -> bool:
        """Force stop any active recording."""
        print(f"{LOG_PREFIXES['PROC']} Force stopping recording")
        
        # Stop OBS recording
        if not self.obs.stop_recording():
            print(f"{LOG_PREFIXES['PROC']} Failed to stop OBS recording")
            return False
        
        return True
