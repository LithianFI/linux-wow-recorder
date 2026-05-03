"""
OBS WebSocket Client for WoW Raid Recorder.
Handles communication with OBS Studio for recording control.
"""

import time
import obsws_python as obs

from constants import (
    DEFAULT_OBS_HOST,
    DEFAULT_OBS_PORT,
    DEFAULT_OBS_TIMEOUT,
    LOG_PREFIXES,
)


class OBSClient:
    """Client for OBS WebSocket communication."""
    
    # ---------------------------------------------------------------------
    # Initialization
    # ---------------------------------------------------------------------
    
    def __init__(self, host: str = DEFAULT_OBS_HOST, 
                 port: int = DEFAULT_OBS_PORT, 
                 password: str = '', 
                 timeout: int = DEFAULT_OBS_TIMEOUT):
        """Initialize OBS client.
        
        Args:
            host: OBS WebSocket host
            port: OBS WebSocket port
            password: OBS WebSocket password
            timeout: Connection timeout in seconds
        """
        self.host = host
        self.port = port
        self.password = password
        self.timeout = timeout
        self.client = None
        self._is_connected = False
        self._cached_record_directory: str = ''
    
    # ---------------------------------------------------------------------
    # Connection Management
    # ---------------------------------------------------------------------
    
    def connect(self) -> bool:
        """Establish connection to OBS WebSocket.
        
        Returns:
            True if connected successfully, False otherwise
        """
        try:
            self.client = obs.ReqClient(
                host=self.host,
                port=self.port,
                password=self.password,
                timeout=self.timeout
            )
            self._is_connected = True
            print(f"{LOG_PREFIXES['OBS']} ✅ Connected to OBS WebSocket")
            return True
            
        except Exception as e:
            print(f"{LOG_PREFIXES['OBS']} ❌ Failed to connect: {e}")
            self._is_connected = False
            return False
    
    def disconnect(self):
        """Disconnect from OBS WebSocket."""
        if self.client and self._is_connected:
            try:
                self.client.disconnect()
                print(f"{LOG_PREFIXES['OBS']} 🔌 Disconnected from OBS")
            except Exception as e:
                print(f"{LOG_PREFIXES['OBS']} ⚠️ Error during disconnect: {e}")
            finally:
                self.client = None
                self._is_connected = False
    
    @property
    def is_connected(self) -> bool:
        """Check if client is connected."""
        return self._is_connected and self.client is not None
    
    # ---------------------------------------------------------------------
    # Recording Control
    # ---------------------------------------------------------------------
    
    def start_recording(self) -> bool:
        """Start OBS recording.
        
        Returns:
            True if recording started successfully, False otherwise
        """
        if not self._ensure_connection():
            return False
        
        try:
            # Check if already recording
            status = self.get_recording_status()
            if status and status.get('is_recording', False):
                print(f"{LOG_PREFIXES['OBS']} ⚠️ Recording already active")
                return True
            
            # Start recording
            self.client.start_record()
            print(f"{LOG_PREFIXES['OBS']} ⏺️ Recording started")
            
            # Brief pause to ensure recording initializes
            time.sleep(0.5)
            return True
            
        except Exception as e:
            print(f"{LOG_PREFIXES['OBS']} ❌ Failed to start recording: {e}")
            return False
    
    def stop_recording(self) -> bool:
        """Stop OBS recording.
        
        Returns:
            True if recording stopped successfully, False otherwise
        """
        if not self._ensure_connection():
            return False
        
        try:
            # Check if recording is active
            status = self.get_recording_status()
            if not status or not status.get('is_recording', False):
                print(f"{LOG_PREFIXES['OBS']} ⚠️ No active recording to stop")
                return True
            
            # Stop recording
            self.client.stop_record()
            print(f"{LOG_PREFIXES['OBS']} ⏹️ Recording stopped")
            
            # Wait for recording to finalize
            time.sleep(1)
            return True
            
        except Exception as e:
            print(f"{LOG_PREFIXES['OBS']} ❌ Failed to stop recording: {e}")
            return False
    
    
    # ---------------------------------------------------------------------
    # Recording Status and Information
    # ---------------------------------------------------------------------
    
    def get_recording_status(self) -> dict:
        """Get current recording status.
        
        Returns:
            Dictionary with recording status information, or None on error
        """
        if not self._ensure_connection():
            return None
        
        try:
            response = self.client.get_record_status()
            status = {}
            
            # Extract available attributes
            attributes = ['output_active', 'output_paused', 'output_timecode',
                         'output_duration', 'output_bytes']
            
            for attr in attributes:
                if hasattr(response, attr):
                    status[attr] = getattr(response, attr)
            
            # Convert to more intuitive names
            status['is_recording'] = status.get('output_active', False)
            status['is_paused'] = status.get('output_paused', False)
            status['duration'] = status.get('output_duration', 0)
            status['bytes'] = status.get('output_bytes', 0)
            
            return status
            
        except Exception as e:
            print(f"[OBS] ⚠️ Failed to get recording status: {e}")
            return None
    
    def get_recording_settings(self) -> dict:
        """Get recording settings including output directory.
        
        Returns:
            Dictionary with recording settings, or None on error
        """
        if not self._ensure_connection():
            if self._cached_record_directory:
                return {'record_directory': self._cached_record_directory}
            return None
        
        try:
            settings = {}
            
            # Get recording directory
            response = self.client.get_record_directory()
            if hasattr(response, 'record_directory'):
                settings['record_directory'] = response.record_directory
                self._cached_record_directory = response.record_directory
            
            # Get output settings if available
            try:
                output_response = self.client.get_output_settings()
                if hasattr(output_response, 'output_settings'):
                    settings.update(output_response.output_settings)
            except Exception:
                pass  # Output settings might not be available
            
            return settings
            
        except Exception as e:
            print(f"[OBS] ⚠️ Failed to get recording settings: {e}")
            if self._cached_record_directory:
                return {'record_directory': self._cached_record_directory}
            return None
    
    def get_last_recording_info(self) -> dict:
        """Get information about the last recording.
        
        Returns:
            Dictionary with last recording information
        """
        settings = self.get_recording_settings()
        if not settings or 'record_directory' not in settings:
            return {}
        
        record_dir = settings['record_directory']
        if not record_dir:
            return {}
        
        return self._find_latest_recording(record_dir)
    
    # ---------------------------------------------------------------------
    # Helper Methods
    # ---------------------------------------------------------------------
    
    def _ensure_connection(self) -> bool:
        """Ensure client is connected to OBS.
        
        Returns:
            True if connected, False otherwise
        """
        if self.is_connected:
            return True
        
        print("[OBS] 🔌 Not connected to OBS, attempting to reconnect...")
        return self.connect()
    
    def _find_latest_recording(self, directory: str) -> dict:
        """Find the most recent recording file in a directory (including date subfolders)."""
        from pathlib import Path

        try:
            record_path = Path(directory)
            if not record_path.exists():
                return {}

            video_extensions = {'.mp4', '.mkv', '.flv', '.mov', '.ts',
                                 '.m3u8', '.avi', '.wmv'}

            # rglob descends into date subfolders automatically
            video_files = [
                f for f in record_path.rglob('*')
                if f.is_file() and f.suffix.lower() in video_extensions
            ]

            if not video_files:
                return {}

            latest_file = max(video_files, key=lambda f: f.stat().st_mtime)

            return {
                'path': str(latest_file),
                'name': latest_file.name,
                'size': latest_file.stat().st_size,
                'modified': latest_file.stat().st_mtime,
            }

        except Exception as e:
            print(f"[OBS] ⚠️ Error finding recordings: {e}")
            return {}
    
    # ---------------------------------------------------------------------
    # Context Manager Support
    # ---------------------------------------------------------------------
    
    def __enter__(self):
        """Context manager entry."""
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.disconnect()
    
    # ---------------------------------------------------------------------
    # String Representation
    # ---------------------------------------------------------------------
    
    def __str__(self) -> str:
        """String representation of OBS client."""
        status = "Connected" if self.is_connected else "Disconnected"
        return f"OBSClient(host={self.host}:{self.port}, status={status})"