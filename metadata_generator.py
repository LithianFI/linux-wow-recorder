"""
Metadata Generator for WoW Raid Recorder.
Generates WCR-compatible JSON metadata files for recordings.
"""

import json
import hashlib
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime


class RecordingMetadata:
    """Generates metadata JSON files compatible with Warcraft Recorder format."""
    
    def __init__(self):
        """Initialize metadata generator."""
        self.reset()
    
    def reset(self):
        """Reset metadata for a new recording."""
        self.category = "Raids"
        self.zone_id = 0
        self.zone_name = "Unknown Raid"
        self.flavour = "Retail"
        self.encounter_id = None
        self.encounter_name = None
        self.difficulty_id = None
        self.difficulty = None
        self.duration = 0
        self.result = False  # True = kill, False = wipe
        self.player_info = {}
        self.deaths = []
        self.overrun = 0  # Time after boss died
        self.combatants = []
        self.start_timestamp = None
        self.unique_hash = None
        self.boss_percent = 0
        self.app_version = "1.0.0"  # WoW Raid Recorder version
    
    def set_encounter_info(self, encounter_id: int, encounter_name: str, 
                          difficulty_id: int, zone_id: int = 0, zone_name: str = "Unknown Raid"):
        """Set basic encounter information."""
        self.encounter_id = encounter_id
        self.encounter_name = encounter_name
        self.difficulty_id = difficulty_id
        self.zone_id = zone_id
        self.zone_name = zone_name
        self.difficulty = self._get_difficulty_shorthand(difficulty_id)
    
    def set_player_info(self, guid: str, name: str, realm: str, spec_id: int, team_id: int = 1):
        """Set information about the recording player."""
        self.player_info = {
            "_GUID": guid,
            "_teamID": team_id,
            "_specID": spec_id,
            "_name": name,
            "_realm": realm
        }
    
    def add_combatant(self, guid: str, name: str, realm: str, spec_id: int, team_id: int):
        """Add a combatant to the encounter."""
        combatant = {
            "_GUID": guid,
            "_teamID": team_id,
            "_specID": spec_id,
            "_name": name,
            "_realm": realm
        }
        
        # Avoid duplicates
        if combatant not in self.combatants:
            self.combatants.append(combatant)
    
    def add_death(self, player_guid: str, timestamp_ms: int):
        """Add a player death event."""
        # Deaths are just timestamps in WCR format
        self.deaths.append(timestamp_ms)
    
    def set_result(self, is_kill: bool, duration: float, boss_percent: float = 0):
        """Set encounter result."""
        self.result = is_kill
        self.duration = int(duration)
        self.boss_percent = int(boss_percent) if is_kill else int(boss_percent)
    
    def set_start_time(self, timestamp_ms: int):
        """Set encounter start timestamp."""
        self.start_timestamp = timestamp_ms
        
        # Generate unique hash based on timestamp and encounter
        hash_input = f"{timestamp_ms}_{self.encounter_id}_{self.encounter_name}"
        self.unique_hash = hashlib.md5(hash_input.encode()).hexdigest()
    
    def generate_filename(self, start_datetime: datetime, extension: str = ".mp4") -> str:
        """
        Generate WCR-style filename.
        
        Format: YYYY-MM-DD HH-MM-SS - PlayerName - BossName [Difficulty] (Result).ext
        Example: 2026-01-21 21-11-57 - Isalith - Nexus-King Salhadaar [M] (Kill).mp4
        
        Args:
            start_datetime: DateTime when recording started
            extension: File extension (default .mp4)
            
        Returns:
            Formatted filename string
        """
        # Format: YYYY-MM-DD HH-MM-SS (space between date and time, matching WCR)
        date_str = start_datetime.strftime("%Y-%m-%d %H-%M-%S")

        if not self.encounter_name or not self.player_info:
            # Fallback: use timestamp only, still using WCR-style space separator
            return f"{date_str}{extension}"

        # Player name
        player_name = self.player_info.get("_name", "Unknown")

        # Boss name
        boss_name = self.encounter_name

        # Difficulty shorthand
        difficulty_str = self.difficulty or "N"

        # Result
        result_str = "Kill" if self.result else "Wipe"

        # Combine: YYYY-MM-DD HH-MM-SS - PlayerName - BossName [Difficulty] (Result)
        filename = f"{date_str} - {player_name} - {boss_name} [{difficulty_str}] ({result_str}){extension}"

        # Sanitize filename (remove invalid characters)
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            filename = filename.replace(char, '_')

        return filename

    
    def to_dict(self) -> Dict[str, Any]:
        """Convert metadata to dictionary matching WCR format."""
        return {
            "category": self.category,
            "zoneID": self.zone_id,
            "zoneName": self.zone_name,
            "flavour": self.flavour,
            "encounterID": self.encounter_id,
            "encounterName": self.encounter_name,
            "difficultyID": self.difficulty_id,
            "difficulty": self.difficulty,
            "duration": self.duration,
            "result": self.result,
            "player": self.player_info,
            "deaths": self.deaths,
            "overrun": self.overrun,
            "combatants": self.combatants,
            "start": self.start_timestamp,
            "uniqueHash": self.unique_hash,
            "bossPercent": self.boss_percent,
            "appVersion": self.app_version
        }
    
    def save_json(self, filepath: Path) -> bool:
        """
        Save metadata to JSON file.
        
        Args:
            filepath: Path where to save the JSON file
            
        Returns:
            True if successful, False otherwise
        """
        try:
            data = self.to_dict()
            
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            
            print(f"[Metadata] ✅ Saved metadata to {filepath.name}")
            return True
            
        except Exception as e:
            print(f"[Metadata] ❌ Failed to save metadata: {e}")
            return False
    
    def _get_difficulty_shorthand(self, difficulty_id: int) -> str:
        """Get difficulty shorthand letter."""
        difficulty_map = {
            1: "N",   # Normal (5-player)
            2: "H",   # Heroic (5-player)
            3: "M",   # Mythic
            4: "M+",  # Mythic+
            7: "LFR", # LFR
            14: "N",  # Normal (raid)
            15: "H",  # Heroic (raid)
            16: "M",  # Mythic (raid)
            17: "LFR",# LFR (raid)
            23: "M",  # Mythic
        }
        return difficulty_map.get(difficulty_id, "N")


class DeathParser:
    """Parses UNIT_DIED events from combat logs."""
    
    @staticmethod
    def parse_death_line(line: str) -> Optional[Dict[str, Any]]:
        """
        Parse a UNIT_DIED line from combat log.
        
        Format: TIMESTAMP  UNIT_DIED,sourceGUID,sourceName,sourceFlags,...,destGUID,destName,destFlags,...
        Example: 2/11/2026 21:03:23.2292  UNIT_DIED,0000000000000000,nil,0x80000000,0x80000000,Player-1403-0A330CE3,"Amitrees-Draenor-EU",0x514,0x80000000,0
        
        Args:
            line: Combat log line
            
        Returns:
            Dictionary with death information or None if not a death event
        """
        if "UNIT_DIED" not in line:
            return None
        
        try:
            # Split timestamp and event
            parts = line.split("  ", 1)
            if len(parts) < 2:
                return None
            
            timestamp_str = parts[0].strip()
            event_data = parts[1].strip()
            
            # Parse timestamp (format: M/D/YYYY HH:MM:SS.ssss)
            timestamp = DeathParser._parse_timestamp(timestamp_str)
            
            # Split event data by comma
            fields = event_data.split(",")
            
            if len(fields) < 8:
                return None
            
            # Extract player GUID (field 4) and name (field 5)
            player_guid = fields[4].strip()
            player_name = fields[5].strip().strip('"')
            
            # Only track player deaths
            if not player_guid.startswith("Player-"):
                return None
            
            return {
                "timestamp": timestamp,
                "guid": player_guid,
                "name": player_name,
            }
            
        except Exception as e:
            print(f"[DeathParser] Error parsing death line: {e}")
            return None
    
    @staticmethod
    def _parse_timestamp(timestamp_str: str) -> int:
        """
        Parse combat log timestamp to milliseconds.
        
        Args:
            timestamp_str: Timestamp string (M/D/YYYY HH:MM:SS.ssss)
            
        Returns:
            Timestamp in milliseconds since epoch
        """
        try:
            # Example: 2/11/2026 21:03:23.2292
            dt_str, ms_str = timestamp_str.rsplit(".", 1)
            dt = datetime.strptime(dt_str, "%m/%d/%Y %H:%M:%S")
            
            # Convert to timestamp and add milliseconds
            timestamp_ms = int(dt.timestamp() * 1000)
            timestamp_ms += int(ms_str[:3])  # First 3 digits of fractional seconds
            
            return timestamp_ms
            
        except Exception as e:
            print(f"[DeathParser] Error parsing timestamp: {e}")
            return 0


# Example usage in integration
def create_metadata_from_encounter(encounter_data: Dict[str, Any], 
                                   player_data: Dict[str, Any],
                                   deaths: List[int] = None) -> RecordingMetadata:
    """
    Create metadata object from encounter data.
    
    Args:
        encounter_data: Dict with encounter info (boss_id, boss_name, difficulty_id, etc.)
        player_data: Dict with player info (guid, name, realm, spec_id)
        deaths: List of death timestamps in milliseconds
        
    Returns:
        RecordingMetadata object ready to save
    """
    metadata = RecordingMetadata()
    
    # Set encounter info
    metadata.set_encounter_info(
        encounter_id=encounter_data.get('boss_id', 0),
        encounter_name=encounter_data.get('boss_name', 'Unknown'),
        difficulty_id=encounter_data.get('difficulty_id', 14),
        zone_id=encounter_data.get('zone_id', 0),
        zone_name=encounter_data.get('zone_name', 'Unknown Raid')
    )
    
    # Set player info
    metadata.set_player_info(
        guid=player_data.get('guid', 'Player-0000-00000000'),
        name=player_data.get('name', 'Unknown'),
        realm=player_data.get('realm', 'Unknown'),
        spec_id=player_data.get('spec_id', 0),
        team_id=player_data.get('team_id', 1)
    )
    
    # Set result
    metadata.set_result(
        is_kill=encounter_data.get('is_kill', False),
        duration=encounter_data.get('duration', 0),
        boss_percent=encounter_data.get('boss_percent', 0)
    )
    
    # Set start time
    start_time = encounter_data.get('start_time', int(datetime.now().timestamp() * 1000))
    metadata.set_start_time(start_time)
    
    # Add deaths
    if deaths:
        for death_time in deaths:
            metadata.add_death('', death_time)
    
    return metadata
