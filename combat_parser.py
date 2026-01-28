# combat_parser.py
import csv
import io
import time
import re
from datetime import datetime
from config import Config

class CombatParser:
    def __init__(self, obs_client, state_manager):
        self.obs_client = obs_client
        self.state = state_manager
        self.last_recording_path = None
        
    def _clean_boss_name(self, boss_name):
        """Clean up boss name for use in filename"""
        # Remove special characters and replace spaces
        cleaned = re.sub(r'[<>:"/\\|?*]', '', boss_name)
        cleaned = cleaned.replace(" ", "_")
        cleaned = cleaned.replace("'", "")
        cleaned = cleaned.replace(",", "")
        return cleaned.strip()
    
    def _get_difficulty_name(self, difficulty_id):
        """Convert difficulty ID to readable name"""
        difficulties = {
            1: "Normal",
            2: "Heroic",
            3: "Mythic",
            4: "Mythic+",
            5: "Timewalking",
            9: "40Player",
            14: "Normal",
            15: "Heroic",
            16: "Mythic",
            17: "LFR",
            18: "Event",
            19: "Event",
            20: "Event",
            23: "Mythic",
            24: "Timewalking",
            33: "Timewalking",
        }
        return difficulties.get(difficulty_id, f"Difficulty_{difficulty_id}")
    
    def _generate_filename(self, boss_name, difficulty_name, timestamp=None):
        """Generate filename for the recording"""
        if timestamp is None:
            timestamp = datetime.now()
        
        # Clean boss name
        clean_boss = self._clean_boss_name(boss_name)
        
        # Format timestamp
        date_str = timestamp.strftime("%Y-%m-%d")
        time_str = timestamp.strftime("%H-%M-%S")
        
        # Create filename
        filename = f"{date_str}_{time_str}_{clean_boss}_{difficulty_name}{Config.RECORDING_EXTENSION}"
        return filename
    
    def process_line(self, line: str):
        """
        Handles a single combat-log line that is CSV-formatted.
        Starts on ENCOUNTER_START, stops on ENCOUNTER_END (or UNIT_DIED).
        """
        # 1️⃣ Split off the timestamp (everything before the double-space)
        try:
            ts_part, rest = line.split("  ", 1)  # two spaces separate timestamp
        except ValueError:
            return  # not the expected format

        # 2️⃣ Parse the remainder as CSV (handles quoted fields)
        csv_reader = csv.reader(io.StringIO(rest))
        try:
            fields = next(csv_reader)
        except StopIteration:
            return

        if not fields:
            return

        # 3️⃣ First field is the event name
        event = fields[0].strip().upper()

        # 4️⃣ React to events
        if event == "ENCOUNTER_START":
            if len(fields) >= 6:
                boss_id = int(fields[1])
                boss_name = fields[2]
                difficulty_id = int(fields[3])
                instance_id = int(fields[5])
                
                # Apply boss name override if configured
                if boss_id in Config.BOSS_NAME_OVERRIDES:
                    boss_name = Config.BOSS_NAME_OVERRIDES[boss_id]
                
                if not self.state.recording:
                    print(f"[INFO] ENCOUNTER_START: {boss_name} (ID: {boss_id}) at {ts_part}")
                    
                    # Store encounter details
                    self.state.start_encounter(boss_id, boss_name, difficulty_id, instance_id)
                    
                    # Start recording
                    try:
                        self.obs_client.start_recording()
                        self.state.start_recording()
                        print(f"[INFO] Recording started for {boss_name}")
                    except Exception as e:
                        print(f"[ERROR] Failed to start recording: {e}")
            return

        if event == "ENCOUNTER_END":
            if self.state.recording and self.state.encounter_active:
                if len(fields) >= 5:
                    encounter_result = fields[4]  # 0 = wipe, 1 = kill
                    result_text = "Kill" if encounter_result == "1" else "Wipe"
                    print(f"[INFO] ENCOUNTER_END: {result_text} at {ts_part}")
                    
                    # Get boss info before resetting state
                    boss_name = self.state.current_boss
                    difficulty_id = self.state.difficulty_id
                    
                    # Run this for 3s extra to properly get the end of the encounter
                    time.sleep(3)
                    
                    # Stop recording
                    try:
                        self.obs_client.stop_recording()
                        print(f"[INFO] Recording stopped for {boss_name}")
                        
                        # Get recording info after stopping
                        recording_info = self.obs_client.get_recording_status()
                        if recording_info and 'output_path' in recording_info:
                            self.last_recording_path = recording_info['output_path']
                            print(f"[INFO] Recording saved to: {self.last_recording_path}")
                            
                            # Schedule file rename (if we have boss info)
                            if boss_name and difficulty_id:
                                self._schedule_file_rename(boss_name, difficulty_id)
                        
                    except Exception as e:
                        print(f"[ERROR] Failed to stop recording: {e}")
                    
                    # Reset state
                    self.state.reset()
            return

        # Optional safety-net: stop on any creature death
        if event == "UNIT_DIED":
            if self.state.recording:
                print(f"[INFO] UNIT_DIED detected at {ts_part} – stopping")
                try:
                    self.obs_client.stop_recording()
                    
                    # Get recording info
                    recording_info = self.obs_client.get_recording_status()
                    if recording_info and 'output_path' in recording_info:
                        self.last_recording_path = recording_info['output_path']
                        
                        # Try to rename if we have boss info
                        if self.state.current_boss and self.state.difficulty_id:
                            self._schedule_file_rename(self.state.current_boss, self.state.difficulty_id)
                    
                except Exception as e:
                    print(f"[ERROR] Failed to stop recording: {e}")
                
                self.state.reset()
            return
    
    def _schedule_file_rename(self, boss_name, difficulty_id):
        """Schedule file rename for the next check"""
        # This will be called after a delay to allow OBS to finish writing
        self.pending_rename = {
            'boss_name': boss_name,
            'difficulty_id': difficulty_id,
            'timestamp': time.time()
        }
        print(f"[RENAME] File rename scheduled for {boss_name}")
    
    def check_and_rename_pending(self):
        """Check for pending file renames and execute them"""
        if hasattr(self, 'pending_rename'):
            # Wait a bit to ensure OBS has finished writing
            if time.time() - self.pending_rename['timestamp'] > 5:  # 5 second delay
                boss_name = self.pending_rename['boss_name']
                difficulty_id = self.pending_rename['difficulty_id']
                
                # Get difficulty name
                difficulty_name = self._get_difficulty_name(difficulty_id)
                
                # Try to rename the file
                if self.last_recording_path:
                    self._rename_recording_file(boss_name, difficulty_name)
                
                # Clear pending rename
                del self.pending_rename
    
    def _rename_recording_file(self, boss_name, difficulty_name):
        """Rename the recording file with boss information"""
        try:
            # Get OBS recording path
            recording_path = Path(self.last_recording_path)
            
            if not recording_path.exists():
                print(f"[RENAME] Recording file not found: {recording_path}")
                return
            
            # Generate new filename
            file_time = datetime.fromtimestamp(recording_path.stat().st_mtime)
            new_filename = self._generate_filename(boss_name, difficulty_name, file_time)
            new_path = recording_path.parent / new_filename
            
            # Check if file already exists
            counter = 1
            while new_path.exists():
                new_filename = self._generate_filename(
                    f"{boss_name}_attempt{counter}", 
                    difficulty_name, 
                    file_time
                )
                new_path = recording_path.parent / new_filename
                counter += 1
            
            # Rename the file
            recording_path.rename(new_path)
            print(f"[RENAME] File renamed to: {new_filename}")
            
            # Update last recording path
            self.last_recording_path = str(new_path)
            
        except Exception as e:
            print(f"[ERROR] Failed to rename recording file: {e}")