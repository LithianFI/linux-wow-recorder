"""
Recording processing logic for encounters and dungeons.
"""

import time
from typing import Optional

from combat_parser.events import BossInfo, DungeonInfo


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
    
    def force_stop_recording(self) -> bool:
        """Force stop any active recording."""
        print(f"[PROC] Force stopping recording")
        
        # Stop OBS recording
        if not self.obs.stop_recording():
            print("[PROC] Failed to stop OBS recording")
            return False
        
        return True