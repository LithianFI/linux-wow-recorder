# state_manager.py
import time
from datetime import datetime

class RecordingState:
    def __init__(self):
        self.recording = False
        self.encounter_active = False
        self.current_boss = None
        self.boss_id = None
        self.difficulty_id = None
        self.instance_id = None
        self.encounter_start_time = None
        self.recording_start_time = None
        
    def start_encounter(self, boss_id, boss_name, difficulty_id, instance_id):
        """Record encounter start details"""
        self.encounter_active = True
        self.boss_id = boss_id
        self.current_boss = boss_name
        self.difficulty_id = difficulty_id
        self.instance_id = instance_id
        self.encounter_start_time = time.time()
        print(f"[STATE] Encounter started: {boss_name} (ID: {boss_id})")
    
    def start_recording(self):
        """Mark recording as started"""
        self.recording = True
        self.recording_start_time = time.time()
    
    def reset(self):
        """Reset state to default"""
        self.recording = False
        self.encounter_active = False
        self.current_boss = None
        self.boss_id = None
        self.difficulty_id = None
        self.instance_id = None
        self.encounter_start_time = None
        self.recording_start_time = None
    
    def get_encounter_duration(self):
        """Get encounter duration in seconds"""
        if not self.encounter_start_time:
            return 0
        return int(time.time() - self.encounter_start_time)
    
    def get_recording_duration(self):
        """Get recording duration in seconds"""
        if not self.recording_start_time:
            return 0
        return int(time.time() - self.recording_start_time)
    
    def __str__(self):
        return (f"RecordingState(recording={self.recording}, "
                f"boss={self.current_boss}, boss_id={self.boss_id})")