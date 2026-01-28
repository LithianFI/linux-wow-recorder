# config.py
import re
from pathlib import Path

class Config:
    LOG_DIR = Path.home() / "Games" / "World of Warcraft" / "_retail_" / "Logs"
    LOG_PATTERN = re.compile(r"WoWCombatLog-\d{6}_\d{6}\.txt$")
    
    # OBS Configuration
    OBS_HOST = "localhost"
    OBS_PORT = 4455
    OBS_PASSWORD = ""  # set if you configured a password
    
    # Recording Configuration
    RECORDING_PATH = Path.home() / "Drives" / "Darkshire" / "Linux Vods"  # Default OBS recording path
    RECORDING_EXTENSION = ".mp4"  # Change based on your OBS recording format
    
    # Boss name mappings (optional - for cleanup or translations)
    # Format: {boss_id: "Boss Name"}
    BOSS_NAME_OVERRIDES = {
        2688: "Rashok",
        2687: "The Vigilant Steward, Zskarn",
        # Add more as needed
    }