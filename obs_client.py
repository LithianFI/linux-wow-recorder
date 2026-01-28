# obs_client.py
import obsws_python as obs
import time

class OBSClient:
    def __init__(self, host='localhost', port=4455, password='', timeout=3):
        self.host = host
        self.port = port
        self.password = password
        self.timeout = timeout
        self.ws = None
        
    def connect(self):
        """Establish connection to OBS WebSocket"""
        try:
            self.ws = obs.ReqClient(
                host=self.host, 
                port=self.port, 
                password=self.password, 
                timeout=self.timeout
            )
            print("[OBS] Connected successfully")
            return self.ws
        except Exception as e:
            print(f"[OBS] Failed to connect: {e}")
            raise
    
    def disconnect(self):
        """Disconnect from OBS WebSocket"""
        if self.ws:
            try:
                self.ws.disconnect()
                print("[OBS] Disconnected")
            except Exception as e:
                print(f"[OBS] Error during disconnect: {e}")
    
    def start_recording(self):
        """Tell OBS to start a recording"""
        if not self.ws:
            raise ConnectionError("Not connected to OBS")
        
        try:
            # Get current recording status first
            response = self.ws.get_record_status()
            if response.record_active:
                print("[OBS] Recording already active")
                return
            
            # Start recording
            self.ws.start_record()
            print("[OBS] Recording started")
            
            # Wait a moment for recording to initialize
            time.sleep(0.5)
            
        except Exception as e:
            print(f"[OBS] *** FAILED to start recording: {e}")
            raise
    
    def stop_recording(self):
        """Tell OBS to stop the current recording"""
        if not self.ws:
            raise ConnectionError("Not connected to OBS")
        
        try:
            # Get current recording status
            response = self.ws.get_record_status()
            if not response.record_active:
                print("[OBS] No active recording to stop")
                return
            
            # Stop recording
            self.ws.stop_record()
            print("[OBS] Recording stopped")
            
            # Wait for recording to finalize
            time.sleep(1)
            
        except Exception as e:
            print(f"[OBS] *** FAILED to stop recording: {e}")
            raise
    
    def get_recording_status(self):
        """Get current recording status and file path"""
        if not self.ws:
            raise ConnectionError("Not connected to OBS")
        
        try:
            response = self.ws.get_record_status()
            return {
                'recording_active': response.record_active,
                'output_path': response.output_path if hasattr(response, 'output_path') else None,
                'recording_time': response.record_time if hasattr(response, 'record_time') else 0
            }
        except Exception as e:
            print(f"[OBS] Failed to get recording status: {e}")
            return None
    
    def get_recording_settings(self):
        """Get current recording settings"""
        if not self.ws:
            raise ConnectionError("Not connected to OBS")
        
        try:
            response = self.ws.get_record_directory()
            return {
                'record_directory': response.record_directory
            }
        except Exception as e:
            print(f"[OBS] Failed to get recording settings: {e}")
            return None