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
    
    def start_dungeon(self, dungeon_id: int, dungeon_name: str, 
                     dungeon_level: int, timestamp: str = ""):
        """Start tracking a Mythic+ dungeon run.
        
        Args:
            dungeon_id: Dungeon ID
            dungeon_name: Dungeon name
            dungeon_level: Key level
            timestamp: Log timestamp when dungeon started
        """
        self.dungeon_active = True
        self.dungeon_id = dungeon_id
        self.dungeon_name = dungeon_name
        self.dungeon_level = dungeon_level
        self.dungeon_start_time = time.time()
        self.dungeon_start_timestamp = timestamp
        self.last_activity_time = time.time()
        
        print(f"[STATE] 🏁 M+ Dungeon started: {dungeon_name} (+{dungeon_level})")
    
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
        
        # Dungeon state
        self.dungeon_active = False
        self.dungeon_id = None
        self.dungeon_name = None
        self.dungeon_level = None
        self.dungeon_start_time = None
        self.dungeon_start_timestamp = None
        self.last_activity_time = None
    
    def update_activity(self):
        """Update last activity timestamp."""
        self.last_activity_time = time.time()
    
    def is_dungeon_idle(self, timeout_seconds: int) -> bool:
        """Check if dungeon has been idle for too long.
        
        Args:
            timeout_seconds: Maximum idle time in seconds
            
        Returns:
            True if dungeon is idle beyond timeout, False otherwise
        """
        if not self.dungeon_active or not self.last_activity_time:
            return False
        
        idle_time = time.time() - self.last_activity_time
        return idle_time > timeout_seconds
    
    # ---------------------------------------------------------------------
    # State Queries
    # ---------------------------------------------------------------------
    
    @property
    def is_recording(self) -> bool:
        """Check if currently recording."""
        return self.recording and (self.encounter_active or self.dungeon_active)
    
    @property
    def has_boss_info(self) -> bool:
        """Check if boss information is available."""
        return self.boss_name is not None and self.difficulty_id is not None
    
    @property
    def has_dungeon_info(self) -> bool:
        """Check if dungeon information is available."""
        return self.dungeon_name is not None and self.dungeon_level is not None
    
    def get_encounter_duration(self) -> float:
        """Get current encounter duration in seconds.
        
        Returns:
            Duration in seconds, or 0 if no encounter active
        """
        if self.encounter_active and self.encounter_start_time:
            return time.time() - self.encounter_start_time
        elif self.dungeon_active and self.dungeon_start_time:
            return time.time() - self.dungeon_start_time
        return 0.0
    
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
        if self.dungeon_active:
            if self.recording:
                duration = self.get_recording_duration()
                return f"RecordingState(M+ RECORDING {self.dungeon_name} +{self.dungeon_level}, {duration:.1f}s)"
            else:
                return f"RecordingState(M+ {self.dungeon_name} +{self.dungeon_level}, not recording)"
        
        if not self.encounter_active:
            return "RecordingState(IDLE)"
        
        boss_info = f"{self.boss_name}" if self.boss_name else "Unknown"
        
        if self.recording:
            duration = self.get_recording_duration()
            return f"RecordingState(RECORDING {boss_info}, {duration:.1f}s)"
        else:
            return f"RecordingState(ENCOUNTER {boss_info}, not recording)"
    
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
            'dungeon_active': self.dungeon_active,
            'dungeon_id': self.dungeon_id,
            'dungeon_name': self.dungeon_name,
            'dungeon_level': self.dungeon_level,
            'dungeon_start_timestamp': self.dungeon_start_timestamp,
            'last_activity_time': self.last_activity_time,
        }