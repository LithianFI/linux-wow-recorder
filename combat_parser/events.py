"""
Event classes for combat log parsing.
"""

import csv
import io
import re
from dataclasses import dataclass
from typing import Optional, List
from datetime import datetime


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
    
    @property
    def difficulty_name(self) -> str:
        """Get difficulty name."""
        difficulties = {
            1: "Normal", 2: "Heroic", 3: "Mythic", 4: "Mythic+",
            5: "Timewalking", 7: "LFR", 9: "40Player",
            14: "Normal", 15: "Heroic", 16: "Mythic", 17: "LFR",
            23: "Mythic", 24: "Timewalking", 33: "Timewalking",
        }
        return difficulties.get(self.difficulty_id, f"Difficulty_{self.difficulty_id}")


@dataclass 
class DungeonInfo:
    """Information about a Mythic+ dungeon run."""
    dungeon_id: int
    name: str
    dungeon_level: int
    timestamp: str = ""
    
    @property
    def formatted_name(self) -> str:
        """Get dungeon name formatted for filename."""
        # Remove special characters and replace spaces
        cleaned = re.sub(r'[<>:"/\\|?*]', '', self.name)
        cleaned = cleaned.replace(" ", "_")
        cleaned = cleaned.replace("'", "")
        cleaned = cleaned.replace(",", "")
        cleaned = cleaned.replace(":", "")
        cleaned = cleaned.replace("-", "_")
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
        line = self.raw_line.strip()
        if not line:
            return
        
        try:
            # Find the first double space to separate timestamp from data
            if "  " in line:
                ts_end = line.find("  ")
                self.timestamp = line[:ts_end].strip()
                data = line[ts_end + 2:].strip()
            else:
                # Fallback: split on first space after time
                first_space = line.find(" ")
                if first_space == -1:
                    return
                
                second_space = line.find(" ", first_space + 1)
                if second_space == -1:
                    return
                
                self.timestamp = line[:second_space].strip()
                data = line[second_space + 1:].strip()
            
            # Parse CSV data with proper quote handling
            csv_reader = csv.reader(io.StringIO(data), quotechar='"', delimiter=',')
            
            try:
                row = next(csv_reader)
                # Clean up fields
                self.fields = []
                for field in row:
                    field = field.strip()
                    # Remove surrounding quotes if present
                    if field.startswith('"') and field.endswith('"'):
                        field = field[1:-1]
                    self.fields.append(field)
                
                if self.fields:
                    self.event_type = self.fields[0].upper()
                    
            except StopIteration:
                pass
                
        except Exception as e:
            if "ENCOUNTER" in line or "CHALLENGE_MODE" in line:
                print(f"[PARSER] Parse error: {e}")
                print(f"[PARSER] Line: {line[:100]}...")
    
    @property
    def is_encounter_start(self) -> bool:
        """Check if this is an ENCOUNTER_START event."""
        return self.event_type == "ENCOUNTER_START"
    
    @property
    def is_encounter_end(self) -> bool:
        """Check if this is an ENCOUNTER_END event."""
        return self.event_type == "ENCOUNTER_END"
    
    @property
    def is_dungeon_start(self) -> bool:
        """Check if this is a CHALLENGE_MODE_START event."""
        return self.event_type == "CHALLENGE_MODE_START"
    
    @property
    def is_dungeon_end(self) -> bool:
        """Check if this is a CHALLENGE_MODE_END event."""
        return self.event_type == "CHALLENGE_MODE_END"
    
    @property
    def is_zone_change(self) -> bool:
        """Check if this is a ZONE_CHANGE event."""
        return self.event_type == "ZONE_CHANGE"
    
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
    
    def get_dungeon_info(self) -> Optional[DungeonInfo]:
        """Extract dungeon information from CHALLENGE_MODE_START event."""
        if not self.is_dungeon_start:
            return None
        
        if len(self.fields) < 5:
            return None
        
        try:
            # CHALLENGE_MODE_START,"Tazavesh, the Veiled Market",2441,391,14,[10,9,147]
            # Fields: [0]=CHALLENGE_MODE_START, [1]=zoneName, [2]=instanceID, [3]=challengeModeID, [4]=keystoneLevel
            
            dungeon_name = self.fields[1]
            instance_id = int(self.fields[2])
            keystone_level = int(self.fields[4])
            
            return DungeonInfo(
                dungeon_id=instance_id,
                name=dungeon_name,
                dungeon_level=keystone_level,
                timestamp=self.timestamp
            )
            
        except (ValueError, IndexError) as e:
            print(f"[PARSER] Error parsing dungeon info: {e}")
            return None
    
    def get_encounter_end_info(self) -> tuple[bool, Optional[str]]:
        """Get kill/wipe status from ENCOUNTER_END event."""
        is_kill = False
        boss_name = None
        
        if self.is_encounter_end and len(self.fields) >= 6:
            try:
                is_kill = self.fields[5] == "1"
                boss_name = self.fields[2] if len(self.fields) > 2 else None
            except (ValueError, IndexError):
                pass
        
        return is_kill, boss_name
    
    def get_dungeon_end_info(self) -> tuple[bool, Optional[str]]:
        """Get success status from CHALLENGE_MODE_END event."""
        is_success = False
        dungeon_name = None
        
        if self.is_dungeon_end and len(self.fields) >= 5:
            try:
                is_success = self.fields[4] == "1"
                dungeon_name = self.fields[2] if len(self.fields) > 2 else None
            except (ValueError, IndexError):
                pass
        
        return is_success, dungeon_name
    
    def is_valid(self) -> bool:
        """Check if this is a valid parsable event."""
        return bool(self.event_type) and len(self.fields) > 0
    
    def __str__(self) -> str:
        return f"CombatEvent({self.event_type} at {self.timestamp[:19]})"