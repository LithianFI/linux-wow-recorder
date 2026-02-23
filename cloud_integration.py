"""
Cloud Upload Integration for WoW Raid Recorder.
Integrates cloud upload functionality with the existing recording system.
"""

import asyncio
from pathlib import Path
from typing import Optional, Callable
from threading import Thread

from cloud_upload import (
    CloudUploadProvider,
    WarcraftRecorderCloud,
    CloudUploadQueue,
    VideoMetadata,
    UploadProgress,
)


class CloudUploadManager:
    """
    Manages cloud upload integration with the recording system.
    Handles automatic uploads after recording completion.
    """
    
    def __init__(self, config):
        """
        Initialize cloud upload manager.
        
        Args:
            config: ConfigManager instance with cloud settings
        """
        self.config = config
        self.provider: Optional[CloudUploadProvider] = None
        self.upload_queue: Optional[CloudUploadQueue] = None
        self.event_loop: Optional[asyncio.AbstractEventLoop] = None
        self.upload_thread: Optional[Thread] = None
        self.progress_callback: Optional[Callable] = None
        
        print("[Cloud Manager] Initialized")
    
    async def initialize(self) -> bool:
        """
        Initialize cloud upload provider based on configuration.
        
        Returns:
            True if initialization successful, False otherwise
        """
        if not self.config.CLOUD_UPLOAD_ENABLED:
            print("[Cloud Manager] Cloud upload is disabled in config")
            return False
        
        provider_name = self.config.CLOUD_UPLOAD_PROVIDER
        
        # Initialize provider based on config
        if provider_name == 'warcraft_recorder':
            success = await self._init_warcraft_recorder()
        elif provider_name == 'google_drive':
            print("[Cloud Manager] Google Drive not yet implemented")
            return False
        elif provider_name == 'proton_drive':
            print("[Cloud Manager] Proton Drive not yet implemented")
            return False
        else:
            print(f"[Cloud Manager] Unknown provider: {provider_name}")
            return False
        
        if success:
            # Initialize upload queue
            self.upload_queue = CloudUploadQueue(self.provider)
            if self.progress_callback:
                self.upload_queue.add_progress_callback(self.progress_callback)
            
            # Start queue processing in background
            self._start_queue_processor()
            
            print("[Cloud Manager] ✅ Cloud upload initialized and ready")
        
        return success
    
    async def _init_warcraft_recorder(self) -> bool:
        """Initialize Warcraft Recorder cloud provider."""
        username = self.config.WCR_USERNAME
        password = self.config.WCR_PASSWORD
        guild = self.config.WCR_GUILD
        
        if not username or not password or not guild:
            print("[Cloud Manager] ❌ Missing Warcraft Recorder credentials in config")
            return False
        
        print(f"[Cloud Manager] Initializing Warcraft Recorder Cloud (guild: {guild})")
        
        self.provider = WarcraftRecorderCloud(username, password, guild)
        
        # Authenticate
        authenticated = await self.provider.authenticate()
        
        if authenticated:
            storage_info = self.provider.get_storage_info()
            print(f"[Cloud Manager] Storage: {storage_info['usage_gb']}GB / {storage_info['limit_gb']}GB "
                  f"({storage_info['usage_percent']}%)")
        
        return authenticated
    
    def _start_queue_processor(self):
        """Start the upload queue processor in a background thread."""
        if self.upload_thread and self.upload_thread.is_alive():
            print("[Cloud Manager] Queue processor already running")
            return
        
        def run_async_loop():
            """Run asyncio event loop in background thread."""
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self.event_loop = loop
            
            try:
                loop.run_until_complete(self.upload_queue.process_queue())
            finally:
                loop.close()
        
        self.upload_thread = Thread(target=run_async_loop, daemon=True)
        self.upload_thread.start()
        print("[Cloud Manager] Queue processor started")
    
    def queue_upload(
        self,
        file_path: Path,
        boss_name: Optional[str] = None,
        difficulty: Optional[str] = None,
        duration: Optional[float] = None,
        encounter_id: Optional[int] = None,
        start_time: Optional[str] = None,
        result: Optional[str] = None,
        category: str = 'raid'
    ) -> bool:
        """
        Queue a recording for upload to cloud storage.
        
        Args:
            file_path: Path to the recording file
            boss_name: Name of the boss/encounter
            difficulty: Difficulty level (Normal, Heroic, Mythic, etc.)
            duration: Duration in seconds
            encounter_id: Encounter ID
            start_time: Start timestamp
            result: Result ('kill' or 'wipe')
            category: Category ('raid', 'dungeon', 'manual')
            
        Returns:
            True if queued successfully, False otherwise
        """
        if not self.config.CLOUD_UPLOAD_ENABLED:
            return False
        
        if not self.upload_queue:
            print("[Cloud Manager] Upload queue not initialized")
            return False
        
        # Build metadata
        metadata = VideoMetadata(
            video_name=file_path.name,
            file_path=str(file_path),
            file_size=file_path.stat().st_size,
            boss_name=boss_name,
            difficulty=difficulty,
            duration=duration,
            encounter_id=encounter_id,
            start_time=start_time,
            result=result,
            category=category,
        )
        
        # Add to queue
        success = self.upload_queue.add_to_queue(file_path, metadata)
        
        if success:
            print(f"[Cloud Manager] ✅ Queued for upload: {file_path.name}")
        
        return success
    
    def set_progress_callback(self, callback: Callable[[UploadProgress], None]):
        """
        Set callback function for upload progress updates.
        
        Args:
            callback: Function that receives UploadProgress objects
        """
        self.progress_callback = callback
        if self.upload_queue:
            self.upload_queue.add_progress_callback(callback)
    
    def get_queue_status(self) -> dict:
        """Get current upload queue status."""
        if not self.upload_queue:
            return {
                'enabled': False,
                'queue_size': 0,
                'active_upload': None,
            }
        
        status = self.upload_queue.get_status()
        status['enabled'] = self.config.CLOUD_UPLOAD_ENABLED
        status['provider'] = self.config.CLOUD_UPLOAD_PROVIDER
        
        return status
    
    def get_storage_info(self) -> dict:
        """Get cloud storage information."""
        if not self.provider:
            return {}
        
        return self.provider.get_storage_info()
    
    def is_ready(self) -> bool:
        """Check if cloud upload is ready."""
        return (
            self.config.CLOUD_UPLOAD_ENABLED and
            self.provider is not None and
            self.provider.is_authenticated() and
            self.upload_queue is not None
        )
    
    async def shutdown(self):
        """Shutdown cloud upload manager gracefully."""
        print("[Cloud Manager] Shutting down...")
        
        if self.upload_queue and self.upload_queue.is_running:
            print("[Cloud Manager] Waiting for active uploads to complete...")
            # Wait for current upload to complete (max 5 minutes)
            wait_time = 0
            while self.upload_queue.is_running and wait_time < 300:
                await asyncio.sleep(1)
                wait_time += 1
        
        print("[Cloud Manager] Shutdown complete")


# =============================================================================
# Integration Helper Functions
# =============================================================================

def create_cloud_manager(config) -> CloudUploadManager:
    """
    Factory function to create and initialize cloud upload manager.
    
    Args:
        config: ConfigManager instance
        
    Returns:
        CloudUploadManager instance
    """
    manager = CloudUploadManager(config)
    return manager


async def initialize_cloud_upload(config) -> Optional[CloudUploadManager]:
    """
    Initialize cloud upload if enabled in config.
    
    Args:
        config: ConfigManager instance
        
    Returns:
        CloudUploadManager if successful, None otherwise
    """
    if not config.CLOUD_UPLOAD_ENABLED:
        print("[Cloud] Cloud upload disabled in config")
        return None
    
    manager = CloudUploadManager(config)
    success = await manager.initialize()
    
    if success:
        return manager
    
    print("[Cloud] Failed to initialize cloud upload")
    return None


def should_auto_upload(config, recording_duration: float) -> bool:
    """
    Determine if a recording should be automatically uploaded.
    
    Args:
        config: ConfigManager instance
        recording_duration: Duration of the recording in seconds
        
    Returns:
        True if recording should be uploaded, False otherwise
    """
    if not config.CLOUD_UPLOAD_ENABLED:
        return False
    
    if not config.CLOUD_AUTO_UPLOAD:
        return False
    
    # Don't upload very short recordings
    min_duration = config.MIN_RECORDING_DURATION
    if recording_duration < min_duration:
        return False
    
    return True


# =============================================================================
# Example Integration with CombatParser
# =============================================================================

def integrate_with_combat_parser(parser, cloud_manager: CloudUploadManager):
    """
    Example integration with the combat parser.
    
    This shows how to hook cloud upload into the existing recording flow.
    Add this to your CombatParser initialization or RecordingProcessor.
    """
    
    def on_recording_completed(file_path: Path, metadata: dict):
        """
        Callback when a recording is completed and renamed.
        
        Args:
            file_path: Path to the final recording file
            metadata: Dictionary with recording metadata
        """
        if not cloud_manager.is_ready():
            return
        
        # Check if we should auto-upload
        duration = metadata.get('duration', 0)
        if not should_auto_upload(parser.config, duration):
            return
        
        # Queue for upload
        cloud_manager.queue_upload(
            file_path=file_path,
            boss_name=metadata.get('boss_name'),
            difficulty=metadata.get('difficulty'),
            duration=duration,
            encounter_id=metadata.get('encounter_id'),
            start_time=metadata.get('start_time'),
            result=metadata.get('result'),  # 'kill' or 'wipe'
            category=metadata.get('category', 'raid'),
        )
        
        print(f"[Integration] Queued for cloud upload: {file_path.name}")
    
    # Register callback with parser
    # parser.on_recording_completed = on_recording_completed
    
    return on_recording_completed
