"""
Main combat log parser coordinating all components.
"""

import time
import threading
from datetime import datetime
from typing import Optional, List, Callable
from pathlib import Path

from obs_client import OBSClient
from state_manager import RecordingState
from config_manager import ConfigManager

from combat_parser.file_manager import RecordingFileManager
from combat_parser.recording_processor import RecordingProcessor
from combat_parser.dungeon_monitor import DungeonMonitor
from combat_parser.events import CombatEvent, BossInfo, DungeonInfo, parse_player_name_realm
from metadata_generator import RecordingMetadata, RecordingCategory, DeathParser

from constants import LOG_PREFIXES

_RELEVANT_EVENTS = frozenset((
    "ENCOUNTER_START", "ENCOUNTER_END",
    "CHALLENGE_MODE_START", "CHALLENGE_MODE_END",
    "ZONE_CHANGE", "COMBATANT_INFO", "UNIT_DIED",
))


_LOCAL_PLAYER_FLAGS = frozenset({'0x511', '0x10511'})


class CombatParser:
    """Main parser that coordinates combat log parsing and recording actions."""

    def __init__(self, obs_client: OBSClient, state_manager: RecordingState,
                 config_manager: ConfigManager):
        self.obs = obs_client
        self.state = state_manager
        self.config = config_manager

        # Initialize components
        self.file_manager = RecordingFileManager(config_manager, obs_client)
        self.processor = RecordingProcessor(obs_client, self.file_manager, config_manager)
        self.dungeon_monitor = DungeonMonitor(state_manager, config_manager, self._handle_dungeon_timeout)

        # Thread management
        self._active_threads: List[threading.Thread] = []
        self._cleanup_completed_threads()

        # Event callbacks for frontend
        self.on_event: Optional[Callable] = None
        self.on_recording_saved: Optional[Callable] = None

        # Callback to retrieve the current log file path from LogMonitor
        # Set by run.py after both parser and log_monitor are created
        self.get_log_path: Optional[Callable] = None

        # Metadata tracking
        self.current_metadata = RecordingMetadata()
        self.player_guid = None
        self.player_name = None
        self.player_realm = None
        self.player_spec_id = None
        self.encounter_start_time = None

        # Log timestamps for the current encounter window (used for death scanning)
        self.encounter_start_log_timestamp: Optional[str] = None
        self.encounter_end_log_timestamp: Optional[str] = None

        # Throttle update_activity() calls — no need to call time.time() on every line
        self._last_activity_update: float = 0.0

        # Start dungeon monitor
        self.dungeon_monitor.start()

    def process_line(self, line: str):
        """Process a single combat log line."""
        # Always try to identify the player first, before any other processing.
        # This runs on every line but short-circuits immediately once resolved.
        if not (self.player_guid and self.player_name):
            self._try_identify_player(line)

        # Throttled activity update — dungeon monitor only checks every 5 s.
        if self.state.dungeon_active:
            now = time.time()
            if now - self._last_activity_update >= 1.0:
                self.state.update_activity()
                self._last_activity_update = now

        # Fast pre-filter: skip CSV parse for lines with no actionable tokens.
        if not any(tok in line for tok in _RELEVANT_EVENTS):
            return

        # Parse the line
        event = CombatEvent(line)
        if not event.is_valid():
            return

        # Handle the event - prioritize dungeons over encounters
        if event.is_dungeon_start:
            self._handle_dungeon_start(event)
        elif event.is_dungeon_end:
            self._handle_dungeon_end(event, "dungeon_complete")
        elif event.is_zone_change:
            self._handle_zone_change(event)
        elif event.is_encounter_start:
            self._handle_encounter_start(event)
        elif event.is_encounter_end:
            self._handle_encounter_end(event)

        # Track deaths — guard with string check before the heavier parse call.
        # DeathParser.parse_death_line() does its own "UNIT_DIED" check internally,
        # but calling the function at all (frame creation, argument passing) on every
        # line during a pull is measurable overhead.
        if self.config.TRACK_PLAYER_DEATHS and (self.state.encounter_active or self.state.dungeon_active):
            if "UNIT_DIED" in line:       
                death_info = DeathParser.parse_death_line(line)
                if death_info:
                    self.encounter_deaths.append({
                        'timestamp': death_info['timestamp'],
                        'name': death_info['name'],
                    })
                    print(f"{LOG_PREFIXES['PARSER']} 💀 Player death: {death_info['name']}")

        # Grab specID from COMBATANT_INFO once we know the player GUID
        if self.player_guid and self.player_spec_id is None and 'COMBATANT_INFO' in line:
            self._parse_combatant_info(line)
        
    def start_manual_recording(self) -> bool:
        """Start a manual recording triggered by the user."""
        if self.state.is_recording or self.state.manual_recording:
            print(f"{LOG_PREFIXES['PARSER']} Manual recording requested but already recording")
            return False

        print(f"{LOG_PREFIXES['PARSER']} Starting manual recording...")

        if not self.obs.start_recording():
            print(f"{LOG_PREFIXES['PARSER']} Failed to start OBS recording for manual session")
            return False

        self.state.start_manual_recording()
        self.state.start_recording()
        self.encounter_start_time = datetime.now()

        # Reset metadata to a clean manual baseline
        self.current_metadata.reset()
        self.current_metadata.category = RecordingCategory.MANUAL
        if self.player_guid and self.player_name:
            self.current_metadata.set_player_info(
            guid=self.player_guid,
            name=self.player_name,
            realm=self.player_realm or "Unknown",
            spec_id=self.player_spec_id or 0,
        )

        self._emit_event('MANUAL_RECORDING_START', '', {'boss_name': 'Manual'})
        print(f"{LOG_PREFIXES['PARSER']} Manual recording started")
        return True


    def stop_manual_recording(self) -> bool:
        """Stop an active manual recording."""
        if not self.state.manual_recording:
            print(f"{LOG_PREFIXES['PARSER']} Stop manual recording requested but no manual recording active")
            return False

        print(f"{LOG_PREFIXES['PARSER']} Stopping manual recording...")
        recording_duration = self.state.get_recording_duration()

        boss_info = BossInfo(
            boss_id=0,
            name="Manual",
            difficulty_id=0,
            instance_id=0,
            timestamp=datetime.now().strftime('%H:%M:%S'),
        )

        self._finalize_metadata(is_kill=True, duration=recording_duration)
        self._emit_event('MANUAL_RECORDING_STOP', '', {'duration': round(recording_duration, 1)})

        # Reset state before the background thread so is_recording becomes False immediately
        self.state.reset()

        # Reuse the existing encounter-end pipeline: stops OBS + renames file
        self._start_thread(self._process_encounter_end_thread, boss_info, recording_duration)

        print(f"{LOG_PREFIXES['PARSER']} Manual recording stopped ({recording_duration:.1f}s)")
        return True    


    def _try_identify_player(self, line: str):
        """Attempt to identify the player GUID and name from any combat log line.

        Regular combat events carry source and dest unit info in a predictable
        position. We look for any Player- GUID paired with a quoted name and
        use the unit flags to confirm it is the local player (0x511 = 
        AFFILIATION_MINE | REACTION_FRIENDLY | CONTROL_PLAYER | TYPE_PLAYER).

        Format (after the double-space timestamp):
          EVENT,srcGUID,"srcName",srcFlags,srcRaidFlags,
                dstGUID,"dstName",dstFlags,dstRaidFlags,...

        We operate on the raw line here rather than going through CombatEvent
        so that this stays as cheap as possible — it runs on every single line.
        """
        # Quick pre-check: must contain a Player- GUID
        if 'Player-' not in line:
            return

        try:
            # Split off the timestamp
            ts_split = line.split('  ', 1)
            if len(ts_split) < 2:
                return
            data = ts_split[1].strip()

            # Split into the first 9 fields (event + 4 src fields + 4 dst fields)
            # Use a limit so we don't split the whole (potentially huge) line
            parts = data.split(',', 9)
            if len(parts) < 9:
                return

            # parts[0] = eventType
            # parts[1] = srcGUID   parts[2] = srcName   parts[3] = srcFlags
            # parts[5] = dstGUID   parts[6] = dstName   parts[7] = dstFlags

            for guid_idx, name_idx, flags_idx in ((1, 2, 3), (5, 6, 7)):
                guid = parts[guid_idx].strip()
                flags = parts[flags_idx].strip()

                if not guid.startswith('Player-'):
                    continue
                if flags not in _LOCAL_PLAYER_FLAGS:
                    continue

                raw_name = parts[name_idx].strip().strip('"')
                if not raw_name or raw_name in ('nil', 'Unknown'):
                    continue

                # Name format: "CharName-RealmName-Region"
                # e.g. "Isalith-Ravencrest-EU"
                # We want CharName and RealmName, dropping the trailing region code.
                # Region codes are always 2-3 uppercase letters (EU, US, TW, KR, CN).
                name, realm = parse_player_name_realm(raw_name)
                if not name:
                    continue

                self.player_guid = guid
                self.player_name = name
                self.player_realm = realm
                print(f"{LOG_PREFIXES['PARSER']} Player identified: "
                      f"{name}-{realm} (GUID: {guid})")
                return

        except Exception as e:
            print(f"{LOG_PREFIXES['PARSER']} Error in player identification: {e}")
            
    
    def _parse_combatant_info(self, line: str):
        """Extract specID from the player's COMBATANT_INFO line.

        COMBATANT_INFO fields (comma-separated, after the event type):
          [0] COMBATANT_INFO
          [1] playerGUID
          [2] faction
          [3-24] stats (strength, agility, stamina, ...)
          [24] specID   ← index 24 from the start of the data segment
        """
        try:
            ts_split = line.split('  ', 1)
            if len(ts_split) < 2:
                return

            # Split only up to field 26 to avoid parsing the huge talent/gear arrays
            parts = ts_split[1].split(',', 26)
            if len(parts) < 25:
                return

            guid = parts[1].strip()
            if guid != self.player_guid:
                return  # Not the local player's COMBATANT_INFO

            spec_id = int(parts[24].strip())
            if spec_id > 0:
                self.player_spec_id = spec_id
                print(f"{LOG_PREFIXES['PARSER']} Player specID: {spec_id}")

                # ── Patch metadata if an encounter/dungeon is already underway ──
                # _init_metadata_for_* runs at ENCOUNTER_START / CHALLENGE_MODE_START,
                # before COMBATANT_INFO fires, so player_info may have spec_id=0.
                # Update it here now that we have the real value.
                if self.current_metadata.player_info:
                    self.current_metadata.player_info["_specID"] = spec_id
                    print(f"{LOG_PREFIXES['PARSER']} Metadata specID updated: {spec_id}")

        except (ValueError, IndexError):
            pass

    def _handle_dungeon_start(self, event: CombatEvent):
        """Handle CHALLENGE_MODE_START event."""
        # Don't start if already recording a dungeon
        if self.state.dungeon_active:
            return
    
        if self.state.manual_recording: 
            return

        # Extract dungeon information
        dungeon_info = event.get_dungeon_info()
        if not dungeon_info:
            print(f"{LOG_PREFIXES['PARSER']} Could not parse dungeon info from: {event}")
            return

        # Start the dungeon in state
        self.state.start_dungeon(
            dungeon_info.dungeon_id,
            dungeon_info.name,
            dungeon_info.dungeon_level,
            dungeon_info.timestamp
        )

        # Store log timestamp for post-encounter death scanning
        self.encounter_start_log_timestamp = dungeon_info.timestamp
        self.encounter_end_log_timestamp = None

        # Initialize metadata for M+ dungeon
        self._init_metadata_for_dungeon(dungeon_info)

        # Start recording in background thread
        self._start_thread(self._process_dungeon_start_thread, dungeon_info)

        # Emit event for frontend
        self._emit_event('DUNGEON_START', dungeon_info.timestamp, {
            'dungeon_name': dungeon_info.name,
            'dungeon_level': dungeon_info.dungeon_level,
            'dungeon_id': dungeon_info.dungeon_id,
        })

        print(f"{LOG_PREFIXES['PARSER']} Started M+ dungeon: {dungeon_info.name} (+{dungeon_info.dungeon_level})")

    def _handle_dungeon_end(self, event: CombatEvent, reason: str = "dungeon_complete"):
        """Handle CHALLENGE_MODE_END event."""
        if not self.state.dungeon_active:
            return
 
        dungeon_name = self.state.dungeon_name
        dungeon_level = self.state.dungeon_level
        dungeon_duration = self.state.get_encounter_duration()
        is_success, _ = event.get_dungeon_end_info()
 
        dungeon_info = DungeonInfo(
            dungeon_id=self.state.dungeon_id or 0,
            name=dungeon_name or "Unknown Dungeon",
            dungeon_level=dungeon_level or 0,
            timestamp=event.timestamp
        )
 
        self._finalize_metadata(is_kill=is_success, duration=dungeon_duration)
 
        self._emit_event('DUNGEON_END', event.timestamp, {
            'dungeon_name': dungeon_name,
            'dungeon_level': dungeon_level,
            'duration': round(dungeon_duration, 1),
            'is_success': is_success,
            'reason': reason,
        })
 
        self._start_thread(self._process_dungeon_end_thread, dungeon_info, dungeon_duration, reason)
        self.state.reset()
 
        print(f"{LOG_PREFIXES['PARSER']} Ended M+ dungeon: {dungeon_info.name} ({reason})")

    def _handle_zone_change(self, event: CombatEvent):
        """Handle ZONE_CHANGE event during dungeon runs."""
        # Only process if we're in an active dungeon
        if not self.state.dungeon_active:
            return

        print(f"{LOG_PREFIXES['PARSER']} Zone change detected during dungeon run")

        # Check if we changed to a different instance (likely left dungeon)
        try:
            # ZONE_CHANGE format: uiMapID, zoneName
            # If zoneName changes significantly, assume dungeon ended
            if len(event.fields) >= 3:
                new_zone = event.fields[2]
                current_dungeon = self.state.dungeon_name

                # Simple check: if zone doesn't contain dungeon name (case-insensitive)
                if current_dungeon and current_dungeon.lower() not in new_zone.lower():
                    print(f"{LOG_PREFIXES['PARSER']} Zone changed from dungeon to: {new_zone}")
                    self._handle_dungeon_end(event, "zone_change")
        except (IndexError, ValueError):
            pass

    def _handle_dungeon_timeout(self):
        """Handle dungeon timeout due to inactivity."""
        if not self.state.dungeon_active:
            return
        self._handle_dungeon_end(None, "timeout")

    def _handle_encounter_start(self, event: CombatEvent):
        """Handle ENCOUNTER_START event."""
        # Don't start if already recording (either encounter or dungeon)
        if self.state.is_recording:
            return
        
        if self.state.manual_recording:  
            return

        # Extract boss information
        boss_info = event.get_boss_info()
        if not boss_info:
            print(f"{LOG_PREFIXES['PARSER']} Could not parse boss info from: {event}")
            return

        # Apply boss name overrides
        overrides = self.config.BOSS_NAME_OVERRIDES
        if boss_info.boss_id in overrides:
            boss_info.name = overrides[boss_info.boss_id]
    
        # Start the encounter in state
        self.state.start_encounter(
            boss_info.boss_id, boss_info.name,
            boss_info.difficulty_id, boss_info.instance_id
        )

        # Store log timestamp for post-encounter death scanning
        self.encounter_start_log_timestamp = boss_info.timestamp
        self.encounter_end_log_timestamp = None

        # Initialize metadata for encounter
        self._init_metadata_for_encounter(boss_info)

        # Start recording in background thread
        self._start_thread(self._process_encounter_start_thread, boss_info)

        # Emit event for frontend
        self._emit_event('ENCOUNTER_START', boss_info.timestamp, {
            'boss_name': boss_info.name,
            'difficulty_id': boss_info.difficulty_id,
        })

        print(f"{LOG_PREFIXES['PARSER']} Started encounter: {boss_info.name}")

    def _handle_encounter_end(self, event: CombatEvent):
        """Handle ENCOUNTER_END event."""
        if not self.state.encounter_active:
            return
 
        boss_name = self.state.boss_name
        difficulty_id = self.state.difficulty_id
        encounter_duration = self.state.get_encounter_duration()
        is_kill, _ = event.get_encounter_end_info()
 
        boss_info = BossInfo(
            boss_id=self.state.boss_id or 0,
            name=boss_name or "Unknown",
            difficulty_id=difficulty_id or 0,
            instance_id=self.state.instance_id or 0,
            timestamp=event.timestamp
        )
 
        self._finalize_metadata(is_kill=is_kill, duration=encounter_duration)
 
        self._emit_event('ENCOUNTER_END', event.timestamp, {
            'boss_name': boss_name,
            'difficulty_id': difficulty_id,
            'duration': round(encounter_duration, 1),
            'is_kill': is_kill,
        })
 
        self._start_thread(self._process_encounter_end_thread, boss_info, encounter_duration)
        self.state.reset()
 
        print(f"{LOG_PREFIXES['PARSER']} Ended encounter: {boss_info.name}")

    def _init_metadata_for_encounter(self, boss_info: BossInfo):
        """Initialize metadata for a new raid/boss encounter."""
        if not (self.config.GENERATE_METADATA_JSON or self.config.FILE_NAMING_SCHEME == 'wcr'):
            return

        self.current_metadata.reset()

        # Explicit category for boss encounters
        self.current_metadata.category = RecordingCategory.RAIDS

        self.current_metadata.set_encounter_info(
            encounter_id=boss_info.boss_id,
            encounter_name=boss_info.name,
            difficulty_id=boss_info.difficulty_id,
        )

        if self.player_guid and self.player_name:
            self.current_metadata.set_player_info(
                guid=self.player_guid,
                name=self.player_name,
                realm=self.player_realm or "Unknown",
                spec_id=self.player_spec_id or 0,
            )

        start_ms = self._parse_timestamp_to_ms(boss_info.timestamp)
        self.current_metadata.set_start_time(start_ms)
        self.encounter_start_time = datetime.now()

    def _init_metadata_for_dungeon(self, dungeon_info: DungeonInfo):
        """Initialize metadata for a Mythic+ dungeon run."""
        if not (self.config.GENERATE_METADATA_JSON or self.config.FILE_NAMING_SCHEME == 'wcr'):
            return

        self.current_metadata.reset()

        # Explicit category for M+ dungeons
        self.current_metadata.category = RecordingCategory.MYTHIC_PLUS

        self.current_metadata.set_encounter_info(
            encounter_id=dungeon_info.dungeon_id,
            encounter_name=f"{dungeon_info.name} +{dungeon_info.dungeon_level}",
            difficulty_id=8,  # Mythic+ difficulty ID
        )

        if self.player_guid and self.player_name:
            self.current_metadata.set_player_info(
                guid=self.player_guid,
                name=self.player_name,
                realm=self.player_realm or "Unknown",
                spec_id=self.player_spec_id or 0,
            )

        start_ms = self._parse_timestamp_to_ms(dungeon_info.timestamp)
        self.current_metadata.set_start_time(start_ms)
        self.encounter_start_time = datetime.now()

    def _finalize_metadata(self, is_kill: bool, duration: float):
        """Finalize metadata with encounter result."""
        if not (self.config.GENERATE_METADATA_JSON or self.config.FILE_NAMING_SCHEME == 'wcr'):
            return

        self.current_metadata.set_result(
            is_kill=is_kill,
            duration=duration,
            boss_percent=100 if is_kill else 0,
        )

        # Deaths already added to current_metadata by _scan_log_for_deaths()
        # which is called before _finalize_metadata on encounter/dungeon end.

    def _scan_log_for_encounter_data(
            self, 
            start_ts: Optional[str] = None,
            end_ts: Optional[str] = None,
            ):
        """Scan the current log file between encounter start and end timestamps
        to collect UNIT_DIED events (deaths) and COMBATANT_INFO lines (raid members).

        WoW only flushes combat log data to disk after the encounter ends, so
        neither deaths nor combatant info can be collected in real-time -- we must
        read them retroactively once ENCOUNTER_END / CHALLENGE_MODE_END fires.

        COMBATANT_INFO fields (indices after splitting on comma):
          [0] COMBATANT_INFO
          [1] playerGUID
          [2] faction  (0 or 1, maps to _teamID)
          [3..22] stats
          [23] currentSpecID
          (followed by talent/gear arrays)

        Player names are not in COMBATANT_INFO directly -- we build a GUID->name
        map from srcGUID/srcName pairs seen throughout the encounter window.
        """
        if not self.get_log_path:
            print(f"{LOG_PREFIXES['PARSER']} No log path callback set, cannot scan encounter data")
            return

        log_path = self.get_log_path()
        if not log_path or not log_path.exists():
            print(f"{LOG_PREFIXES['PARSER']} Log file not available for encounter data scan")
            return

        start_ts = start_ts or self.encounter_start_log_timestamp
        end_ts = end_ts or self.encounter_end_log_timestamp

        if not start_ts or not end_ts:
            print(f"{LOG_PREFIXES['PARSER']} Missing encounter timestamps for data scan")
            return

        print(f"{LOG_PREFIXES['PARSER']} Scanning log between {start_ts[:19]} and {end_ts[:19]}")

        deaths_found = 0
        combatants_found = 0
        in_window = False

        # GUID -> (name, realm) map built from any combat event in the window
        guid_to_name: dict = {}

        # First pass: collect all lines in the window into memory so we can
        # build the GUID->name map before processing COMBATANT_INFO lines.
        window_lines = []
        try:
            with open(log_path, 'r', encoding='utf-8') as f:
                for line in f:
                    if start_ts in line:
                        in_window = True
                    if not in_window:
                        continue
                    window_lines.append(line)
                    if end_ts in line:
                        break
        except Exception as e:
            print(f"{LOG_PREFIXES['PARSER']} Error reading log for encounter data: {e}")
            return

        # Build GUID->name map from src/dest fields in combat event lines.
        # Standard combat event format (after timestamp double-space):
        #   eventType, srcGUID, srcName, srcFlags, srcRaidFlags,
        #   destGUID, destName, destFlags, destRaidFlags, ...
        # We skip COMBATANT_INFO lines because their field layout is different
        # (field [2] is faction, not a name) and would corrupt the map.
        for line in window_lines:
            try:
                ts_split = line.split('  ', 1)
                if len(ts_split) < 2:
                    continue
                event_data = ts_split[1]
                # Skip COMBATANT_INFO - field layout is incompatible with src/dest parsing
                if event_data.startswith('COMBATANT_INFO'):
                    continue
                parts = event_data.split(',', 9)
                if len(parts) < 7:
                    continue
                for guid_idx, name_idx in ((1, 2), (5, 6)):
                    guid = parts[guid_idx].strip()
                    if not guid.startswith('Player-'):
                        continue
                    raw_name = parts[name_idx].strip().strip('"')
                    if not raw_name or raw_name in ('nil', 'Unknown'):
                        continue
                    if guid not in guid_to_name:
                        # Format: "CharName-Realm-EU" or "CharName-Realm-US" etc.
                        # Strip the trailing region code (2-3 uppercase letters)
                        # then split on the first '-' to get name vs realm.
                        name, realm = parse_player_name_realm(raw_name)
                        guid_to_name[guid] = (name, realm)
            except Exception:
                continue

        guid_to_spec: dict = {}
        for line in window_lines:
            is_combatant = 'COMBATANT_INFO' in line
            is_death = 'UNIT_DIED' in line
 
            if not is_combatant and not is_death:
                continue
 
            if is_death and self.config.TRACK_PLAYER_DEATHS:
                death_info = DeathParser.parse_death_line(line)
                if death_info:
                    spec_id = guid_to_spec.get(death_info['guid'], 0)
                    self.current_metadata.add_death(
                        name=death_info['name'],
                        timestamp_ms=death_info['timestamp'],
                        spec_id=spec_id,
                    )
                    deaths_found += 1
                    print(f"{LOG_PREFIXES['PARSER']} 💀 Death: {death_info['name']}")
 
            if is_combatant:
                try:
                    ts_split = line.split('  ', 1)
                    if len(ts_split) < 2:
                        continue
                    parts = ts_split[1].split(',', 25)
                    if len(parts) < 24:
                        continue
 
                    guid = parts[1].strip()
                    if not guid.startswith('Player-'):
                        continue
 
                    faction = int(parts[2].strip())
                    spec_id = int(parts[23].strip())
 
                    # Always update guid_to_spec so any death lines later in this
                    # same pass can look up the spec_id for this player.
                    if spec_id > 0:
                        guid_to_spec[guid] = spec_id
 
                    if guid not in guid_to_name:
                        continue
 
                    name, realm = guid_to_name[guid]
 
                    if guid == self.player_guid and self.player_spec_id is None and spec_id > 0:
                        self.player_spec_id = spec_id
                        if self.current_metadata.player_info:
                            self.current_metadata.player_info["_specID"] = spec_id
 
                    self.current_metadata.add_combatant(
                        guid=guid,
                        name=name,
                        realm=realm,
                        spec_id=spec_id,
                        team_id=faction,
                    )
                    combatants_found += 1
 
                except (ValueError, IndexError):
                    continue
 
        print(f"{LOG_PREFIXES['PARSER']} Scan complete: {deaths_found} deaths, {combatants_found} combatants found")

    def _parse_timestamp_to_ms(self, timestamp: str) -> int:
        """Parse combat log timestamp to milliseconds."""
        try:
            # Format: "2/11/2026 21:02:44.3002" or "21:02:44.3002"
            if '/' in timestamp:
                dt_str, ms_str = timestamp.rsplit(".", 1)
                dt = datetime.strptime(dt_str, "%m/%d/%Y %H:%M:%S")
            else:
                # Time only, use today's date
                time_str, ms_str = timestamp.rsplit(".", 1)
                today = datetime.now().date()
                dt = datetime.strptime(f"{today} {time_str}", "%Y-%m-%d %H:%M:%S")
            
            timestamp_ms = int(dt.timestamp() * 1000)
            timestamp_ms += int(ms_str[:3])  # First 3 digits of fractional seconds
            return timestamp_ms
        except Exception as e:
            print(f"{LOG_PREFIXES['PARSER']} Error parsing timestamp: {e}")
            return int(datetime.now().timestamp() * 1000)

    def _process_dungeon_start_thread(self, dungeon_info: DungeonInfo):
        """Thread function for starting dungeon recording."""
        success = self.processor.process_dungeon_start(dungeon_info)
        if success:
            self.state.start_recording()

    def _process_dungeon_end_thread(self, dungeon_info: DungeonInfo, duration: float, reason: str):
        """Thread function for ending dungeon recording."""
        time.sleep(3)   # moved from handler — no longer blocks log tailer
 
        result = self.processor.process_dungeon_end(
            dungeon_info,
            duration,
            reason,
            metadata=self.current_metadata if (self.config.GENERATE_METADATA_JSON or self.config.FILE_NAMING_SCHEME == 'wcr') else None,
            start_time=self.encounter_start_time
        )
 
        if result and self.on_recording_saved:
            self.on_recording_saved({
                'duration': duration,
                'boss_name': f"{dungeon_info.name} +{dungeon_info.dungeon_level}",
                'difficulty_id': 4,
                'is_kill': reason == 'dungeon_complete',
                'category': 'dungeon',
            })

    def _process_encounter_start_thread(self, boss_info: BossInfo):
        """Thread function for starting encounter recording."""
        success = self.processor.process_encounter_start(boss_info)
        if success:
            self.state.start_recording()

    def _process_encounter_end_thread(self, boss_info: BossInfo, duration: float):
        """Thread function for ending encounter recording."""
        time.sleep(3)   # moved from handler — no longer blocks log tailer
 
        result = self.processor.process_encounter_end(
            boss_info,
            duration,
            metadata=self.current_metadata if (self.config.GENERATE_METADATA_JSON or self.config.FILE_NAMING_SCHEME == 'wcr') else None,
            start_time=self.encounter_start_time
        )
 
        if result and self.on_recording_saved:
            self.on_recording_saved({
                'duration': duration,
                'boss_name': boss_info.name,
                'difficulty_id': boss_info.difficulty_id,
                'is_kill': self.current_metadata.result,
                'category': 'raid',
            })

    def _start_thread(self, target: Callable, *args):
        """Helper to start a background thread."""
        self._cleanup_completed_threads()  # prune dead entries before adding new one
        thread = threading.Thread(
            target=target,
            args=args,
            daemon=True
        )
        thread.start()
        self._active_threads.append(thread)

    def _emit_event(self, event_type: str, timestamp: str, data: dict):
        """Helper to emit events to frontend."""
        if self.on_event:
            self.on_event({
                'type': event_type,
                'timestamp': timestamp,
                **data
            })

    def _cleanup_completed_threads(self):
        """Remove completed threads from active list."""
        self._active_threads = [t for t in self._active_threads if t.is_alive()]

    def get_status(self) -> dict:
        """Get parser status."""
        return {
            'active_threads': len(self._active_threads),
            'dungeon_monitor_running': self.dungeon_monitor.is_running(),
            'last_renamed_path': str(self.file_manager.last_renamed_path) if self.file_manager.last_renamed_path else None,
        }

    def shutdown(self):
        """Clean shutdown of the parser."""
        print(f"{LOG_PREFIXES['PARSER']} Shutting down...")

        # Stop dungeon monitor
        self.dungeon_monitor.stop()

        # Clean up completed threads
        self._cleanup_completed_threads()

        # Wait for active threads to complete (with timeout)
        for thread in self._active_threads:
            if thread.is_alive():
                thread.join(timeout=5.0)

        self._active_threads.clear()
        print(f"{LOG_PREFIXES['PARSER']} Shutdown complete")
