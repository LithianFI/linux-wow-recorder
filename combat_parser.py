# combat_parser.py (updated with minimum duration check)
import csv
import io
import time
import re
import threading
from datetime import datetime
from pathlib import Path

class CombatParser:
    def __init__(self, obs_client, state_manager, config_manager):
        self.obs_client = obs_client
        self.state = state_manager
        self.config = config_manager
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
        filename = f"{date_str}_{time_str}_{clean_boss}_{difficulty_name}{self.config.RECORDING_EXTENSION}"
        return filename
    
    def _find_latest_recording_file(self):
        """Find the most recent recording file in OBS recording directory"""
        try:
            # Get recording directory from OBS
            settings = self.obs_client.get_recording_settings()
            record_dir = None
            
            if settings and 'record_directory' in settings:
                record_dir = Path(settings['record_directory'])
                print(f"[RENAME] Using OBS recording directory: {record_dir}")
            else:
                # Use fallback from config
                fallback_path = self.config.RECORDING_PATH_FALLBACK
                if fallback_path:
                    record_dir = fallback_path
                    print(f"[RENAME] OBS directory not detected, using fallback: {record_dir}")
                else:
                    print("[RENAME] Could not get recording directory from OBS and no fallback configured")
                    return None
            
            if not record_dir.exists():
                print(f"[RENAME] Recording directory not found: {record_dir}")
                # Try to create it if it's the fallback path
                if record_dir == self.config.RECORDING_PATH_FALLBACK:
                    try:
                        record_dir.mkdir(parents=True, exist_ok=True)
                        print(f"[RENAME] Created recording directory: {record_dir}")
                    except Exception as e:
                        print(f"[RENAME] Failed to create directory: {e}")
                        return None
                else:
                    return None
            
            # Look for video files (common extensions)
            video_extensions = {'.mp4', '.mkv', '.flv', '.mov', '.ts', '.m3u8', '.avi', '.wmv'}
            video_files = []
            
            for file in record_dir.iterdir():
                if file.suffix.lower() in video_extensions and file.is_file():
                    video_files.append(file)
            
            if not video_files:
                print(f"[RENAME] No video files found in {record_dir}")
                return None
            
            # Return the most recently modified file
            latest_file = max(video_files, key=lambda f: f.stat().st_mtime)
            print(f"[RENAME] Found latest recording: {latest_file.name}")
            return latest_file
            
        except Exception as e:
            print(f"[ERROR] Failed to find recording file: {e}")
            return None
    
    def _rename_recording_file(self, boss_name, difficulty_id, recording_duration):
        """Rename the recording file with boss information (called after delay)"""
        try:
            if not self.config.AUTO_RENAME:
                print(f"[RENAME] Auto-rename disabled in config, skipping")
                return
            
            # Check minimum duration
            MIN_RECORDING_DURATION = self.config.MIN_RECORDING_DURATION
            if recording_duration < MIN_RECORDING_DURATION:
                print(f"[RENAME] Recording too short ({recording_duration}s < {MIN_RECORDING_DURATION}s), skipping rename")
                # Optionally, we could delete the short recording file here
                # But for now, we'll just leave it with its original name
                return
                
            print(f"[RENAME] Starting rename process for {boss_name} (duration: {recording_duration}s)...")
            
            # Wait a moment to ensure OBS has finished writing
            # Use delay from config
            time.sleep(self.config.RENAME_DELAY)
            
            # Find the latest recording file
            recording_path = self._find_latest_recording_file()
            if not recording_path:
                print("[RENAME] Could not find recording file to rename")
                return
            
            # Verify the file exists and is not still being written
            if not recording_path.exists():
                print(f"[RENAME] Recording file disappeared: {recording_path}")
                return
            
            # Get file size to check if it's a valid file (not empty or corrupted)
            file_size = recording_path.stat().st_size
            if file_size < 1024:  # Less than 1KB is likely corrupted/empty
                print(f"[RENAME] Recording file too small ({file_size} bytes), likely corrupted, skipping rename")
                return
            
            # Get file size and wait if it's still changing (OBS might still be writing)
            initial_size = recording_path.stat().st_size
            time.sleep(1)
            final_size = recording_path.stat().st_size
            
            if initial_size != final_size:
                print("[RENAME] File size still changing, waiting...")
                time.sleep(2)
                final_size = recording_path.stat().st_size
                
                if initial_size != final_size:
                    print("[RENAME] File still being written, aborting rename")
                    return
            
            # Get difficulty name
            difficulty_name = self._get_difficulty_name(difficulty_id)
            
            # Generate new filename
            file_time = datetime.fromtimestamp(recording_path.stat().st_mtime)
            new_filename = self._generate_filename(boss_name, difficulty_name, file_time)
            new_path = recording_path.parent / new_filename
            
            # Check if file already exists (with max attempts from config)
            counter = 1
            while new_path.exists() and counter <= self.config.MAX_RENAME_ATTEMPTS:
                # For duplicate names, add attempt counter
                boss_with_counter = f"{boss_name}_attempt{counter}"
                new_filename = self._generate_filename(boss_with_counter, difficulty_name, file_time)
                new_path = recording_path.parent / new_filename
                counter += 1
            
            if new_path.exists():
                print(f"[RENAME] Max rename attempts ({self.config.MAX_RENAME_ATTEMPTS}) reached, keeping original name")
                return
            
            # Rename the file
            recording_path.rename(new_path)
            print(f"[RENAME] Successfully renamed to: {new_filename}")
            
            # Update last recording path
            self.last_recording_path = str(new_path)
            
        except Exception as e:
            print(f"[ERROR] Failed to rename recording file: {e}")
            import traceback
            traceback.print_exc()
    
    def _start_rename_thread(self, boss_name, difficulty_id, recording_duration):
        """Start a thread to rename the recording file after a delay"""
        if not self.config.AUTO_RENAME:
            print(f"[RENAME] Auto-rename disabled, not renaming {boss_name}")
            return
            
        rename_thread = threading.Thread(
            target=self._rename_recording_file,
            args=(boss_name, difficulty_id, recording_duration),
            daemon=True
        )
        rename_thread.start()
        print(f"[RENAME] Started rename thread for {boss_name} (duration: {recording_duration}s)")
    
    def process_line(self, line: str):
        """
        Handles a single combat-log line that is CSV-formatted.
        Starts on ENCOUNTER_START, stops on ENCOUNTER_END.
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
                overrides = self.config.BOSS_NAME_OVERRIDES
                if boss_id in overrides:
                    boss_name = overrides[boss_id]
                
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
                # Just log the event without parsing result
                print(f"[INFO] ENCOUNTER_END detected at {ts_part}")
                    
                # Get boss info and recording duration before resetting state
                boss_name = self.state.current_boss
                difficulty_id = self.state.difficulty_id
                recording_duration = self.state.get_recording_duration()
                
                # Run this for 3s extra to properly get the end of the encounter
                time.sleep(3)
                
                # Stop recording
                try:
                    self.obs_client.stop_recording()
                    print(f"[INFO] Recording stopped for {boss_name} (duration: {recording_duration}s)")
                    
                    # Start rename thread after recording stops (pass duration for validation)
                    if boss_name and difficulty_id:
                        self._start_rename_thread(boss_name, difficulty_id, recording_duration)
                    else:
                        print(f"[INFO] No boss info available, not renaming")
                    
                except Exception as e:
                    print(f"[ERROR] Failed to stop recording: {e}")
                
                # Reset state
                self.state.reset()
            return