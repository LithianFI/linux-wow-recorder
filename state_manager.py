"""
State Manager for WoW Raid Recorder.
Tracks the current state of encounters and recordings.
"""

import time
from typing import Optional


class RecordingState:
    """Manages the state of current recording and encounter."""
    
    # ---------------------------------------------------------------------
    # Initialization
    # ---------------------------------------------------------------------
    
    def __init__(self):
        """Initialize a fresh recording state."""
        self._reset_all()
    
    # ---------------------------------------------------------------------
    # State Management
    # ---------------------------------------------------------------------
    
    def start_encounter(self, boss_id: int, boss_name: str, 
                       difficulty_id: int, instance_id: int):
        """Start tracking a new encounter.
        
        Args:
            boss_id: Unique identifier for the boss
            boss_name: Name of the boss
            difficulty_id: Difficulty level ID
            instance_id: Instance/raid ID
        """
        self.encounter_active = True
        self.boss_id = boss_id
        self.boss_name = boss_name
        self.difficulty_id = difficulty_id
        self.instance_id = instance_id
        self.encounter_start_time = time.time()
        
        print(f"[STATE] 🏁 Encounter started: {boss_name} (ID: {boss_id})")
    
    def start_recording(self):
        """Mark recording as started."""
        self.recording = True
        self.recording_start_time = time.time()
        print(f"[STATE] ⏺️ Recording marked as started")
    
    def reset(self):
        """Reset state to default (encounter ended)."""
        print(f"[STATE] 🔄 Resetting state")
        self._reset_all()
    
    def _reset_all(self):
        """Reset all state variables to defaults."""
        # Recording state
        self.recording = False
        self.recording_start_time = None
        
        # Encounter state
        self.encounter_active = False
        self.boss_id = None
        self.boss_name = None
        self.difficulty_id = None
        self.instance_id = None
        self.encounter_start_time = None
    
    # ---------------------------------------------------------------------
    # State Queries
    # ---------------------------------------------------------------------
    
    @property
    def is_recording(self) -> bool:
        """Check if currently recording."""
        return self.recording and self.encounter_active
    
    @property
    def has_boss_info(self) -> bool:
        """Check if boss information is available."""
        return self.boss_name is not None and self.difficulty_id is not None
    
    def get_encounter_duration(self) -> float:
        """Get current encounter duration in seconds.
        
        Returns:
            Duration in seconds, or 0 if no encounter active
        """
        if not self.encounter_start_time:
            return 0.0
        return time.time() - self.encounter_start_time
    
    def get_recording_duration(self) -> float:
        """Get current recording duration in seconds.
        
        Returns:
            Duration in seconds, or 0 if not recording
        """
        if not self.recording_start_time:
            return 0.0
        return time.time() - self.recording_start_time
    
    # ---------------------------------------------------------------------
    # String Representation
    # ---------------------------------------------------------------------
    
    def __str__(self) -> str:
        """Get string representation of current state."""
        if not self.encounter_active:
            return "RecordingState(IDLE)"
        
        boss_info = f"{self.boss_name}" if self.boss_name else "Unknown"
        
        if self.recording:
            duration = self.get_recording_duration()
            return f"RecordingState(RECORDING {boss_name}, {duration:.1f}s)"
        else:
            return f"RecordingState(ENCOUNTER {boss_name}, not recording)"
    
    def summary(self) -> dict:
        """Get summary of current state as dictionary.
        
        Returns:
            Dictionary with current state information
        """
        return {
            'recording': self.recording,
            'encounter_active': self.encounter_active,
            'boss_id': self.boss_id,
            'boss_name': self.boss_name,
            'difficulty_id': self.difficulty_id,
            'instance_id': self.instance_id,
            'encounter_duration': self.get_encounter_duration(),
            'recording_duration': self.get_recording_duration(),
        }