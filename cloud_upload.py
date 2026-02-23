"""
Cloud Upload Module for WoW Raid Recorder.
Provides automated upload to cloud storage services after recording completion.

Supports:
- Warcraft Recorder Cloud (warcraftrecorder.com)
- Future: Google Drive, Proton Drive
"""

import os
import json
import time
import base64
import hashlib
import asyncio
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Dict, Any, List, Callable
from dataclasses import dataclass, asdict
from datetime import datetime

import requests
from requests.auth import HTTPBasicAuth


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class UploadProgress:
    """Tracks upload progress."""
    video_name: str
    total_bytes: int
    uploaded_bytes: int
    status: str  # 'queued', 'uploading', 'completed', 'failed'
    start_time: float
    error: Optional[str] = None
    
    @property
    def progress_percent(self) -> float:
        """Calculate upload progress percentage."""
        if self.total_bytes == 0:
            return 0.0
        return (self.uploaded_bytes / self.total_bytes) * 100
    
    @property
    def upload_speed(self) -> float:
        """Calculate upload speed in bytes/sec."""
        elapsed = time.time() - self.start_time
        if elapsed == 0:
            return 0.0
        return self.uploaded_bytes / elapsed


@dataclass
class VideoMetadata:
    """Metadata for uploaded videos."""
    video_name: str
    file_path: str
    file_size: int
    boss_name: Optional[str] = None
    difficulty: Optional[str] = None
    duration: Optional[float] = None
    encounter_id: Optional[int] = None
    start_time: Optional[str] = None
    result: Optional[str] = None  # 'kill', 'wipe'
    category: str = 'raid'  # 'raid', 'dungeon', 'manual'
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {k: v for k, v in asdict(self).items() if v is not None}


# =============================================================================
# Abstract Base Class
# =============================================================================

class CloudUploadProvider(ABC):
    """Abstract base class for cloud upload providers."""
    
    @abstractmethod
    async def authenticate(self) -> bool:
        """Authenticate with the cloud service."""
        pass
    
    @abstractmethod
    async def upload_video(
        self, 
        file_path: Path, 
        metadata: VideoMetadata,
        progress_callback: Optional[Callable[[UploadProgress], None]] = None
    ) -> bool:
        """Upload a video file to cloud storage."""
        pass
    
    @abstractmethod
    async def delete_video(self, video_name: str) -> bool:
        """Delete a video from cloud storage."""
        pass
    
    @abstractmethod
    def is_authenticated(self) -> bool:
        """Check if currently authenticated."""
        pass
    
    @abstractmethod
    def get_storage_info(self) -> Dict[str, Any]:
        """Get storage usage and limit information."""
        pass


# =============================================================================
# Warcraft Recorder Cloud Provider
# =============================================================================

class WarcraftRecorderCloud(CloudUploadProvider):
    """
    Upload provider for Warcraft Recorder Cloud.
    
    API Documentation based on CloudClient.ts:
    - Production API: https://api.warcraftrecorder.com/api
    - Uses Basic HTTP Authentication
    - Supports multipart uploads for large files (>100MB)
    - Guild-based storage buckets
    """
    
    # API Endpoints
    API_BASE = "https://api.warcraftrecorder.com/api"
    MULTIPART_THRESHOLD = 100 * 1024 * 1024  # 100MB
    
    def __init__(self, username: str, password: str, guild_name: str):
        """
        Initialize Warcraft Recorder Cloud provider.
        
        Args:
            username: WCR account username
            password: WCR account password
            guild_name: Guild/bucket name to upload to
        """
        self.username = username
        self.password = password
        self.guild_name = guild_name
        self.authenticated = False
        self.authorized = False
        
        # Permissions
        self.can_read = False
        self.can_write = False
        self.can_delete = False
        
        # Storage info
        self.usage_bytes = 0
        self.limit_bytes = 0
        
        # Auth header (Basic HTTP Auth)
        auth_string = f"{username}:{password}"
        auth_bytes = auth_string.encode('utf-8')
        self.auth_header = f"Basic {base64.b64encode(auth_bytes).decode('utf-8')}"
        
        print(f"[WCR Cloud] Initialized for guild: {guild_name}")
    
    async def authenticate(self) -> bool:
        """
        Authenticate with Warcraft Recorder Cloud API.
        
        Returns:
            True if authentication successful, False otherwise
        """
        try:
            print(f"[WCR Cloud] Authenticating...")
            
            # Check authentication by getting user info
            response = requests.get(
                f"{self.API_BASE}/user",
                headers={"Authorization": self.auth_header},
                timeout=10
            )
            
            if response.status_code != 200:
                print(f"[WCR Cloud] Authentication failed: {response.status_code}")
                return False
            
            self.authenticated = True
            print(f"[WCR Cloud] ✅ Authenticated as {self.username}")
            
            # Get guild permissions and info
            await self._fetch_guild_info()
            
            return True
            
        except Exception as e:
            print(f"[WCR Cloud] ❌ Authentication error: {e}")
            self.authenticated = False
            return False
    
    async def _fetch_guild_info(self) -> bool:
        """Fetch guild information and permissions."""
        try:
            # First, get user affiliations (list of guilds user has access to)
            response = requests.get(
                f"{self.API_BASE}/user/affiliations",
                headers={"Authorization": self.auth_header},
                timeout=10
            )
            
            if response.status_code != 200:
                print(f"[WCR Cloud] Failed to fetch user affiliations: {response.status_code}")
                print(f"[WCR Cloud] Response: {response.text}")
                return False
            
            affiliations = response.json()
            print(f"[WCR Cloud] Found {len(affiliations)} guild affiliations")
            
            # Find our guild in the affiliations
            guild_affiliation = None
            for affiliation in affiliations:
                if affiliation.get('guildName') == self.guild_name:
                    guild_affiliation = affiliation
                    break
            
            if not guild_affiliation:
                print(f"[WCR Cloud] ❌ User is not affiliated with guild '{self.guild_name}'")
                print(f"[WCR Cloud] Available guilds: {[a.get('guildName') for a in affiliations]}")
                return False
            
            # Extract permissions from affiliation
            self.can_read = guild_affiliation.get('read', False)
            self.can_write = guild_affiliation.get('write', False)
            self.can_delete = guild_affiliation.get('del', False)
            
            self.authorized = self.can_write
            
            print(f"[WCR Cloud] Guild: {self.guild_name}")
            print(f"[WCR Cloud] Permissions - Read: {self.can_read}, Write: {self.can_write}, Delete: {self.can_delete}")
            
            # Now fetch storage usage and limit
            await self._fetch_storage_info()
            
            return True
            
        except Exception as e:
            print(f"[WCR Cloud] Error fetching guild info: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    async def _fetch_storage_info(self) -> bool:
        """Fetch storage usage and limit for the guild."""
        try:
            guild_encoded = requests.utils.quote(self.guild_name)
            
            # Get storage usage
            usage_response = requests.get(
                f"{self.API_BASE}/guild/{guild_encoded}/usage",
                headers={"Authorization": self.auth_header},
                timeout=10
            )
            
            if usage_response.status_code == 200:
                usage_data = usage_response.json()
                self.usage_bytes = usage_data.get('bytes', 0)
            
            # Get storage limit
            limit_response = requests.get(
                f"{self.API_BASE}/guild/{guild_encoded}/limit",
                headers={"Authorization": self.auth_header},
                timeout=10
            )
            
            if limit_response.status_code == 200:
                limit_data = limit_response.json()
                self.limit_bytes = limit_data.get('bytes', 0)
            
            print(f"[WCR Cloud] Storage: {self.usage_bytes / (1024**3):.2f}GB / {self.limit_bytes / (1024**3):.2f}GB")
            
            return True
            
        except Exception as e:
            print(f"[WCR Cloud] Warning: Could not fetch storage info: {e}")
            # Don't fail auth if we can't get storage info
            return True
    
    async def upload_video(
        self, 
        file_path: Path, 
        metadata: VideoMetadata,
        progress_callback: Optional[Callable[[UploadProgress], None]] = None
    ) -> bool:
        """
        Upload a video to Warcraft Recorder Cloud.
        
        Args:
            file_path: Path to the video file
            metadata: Video metadata
            progress_callback: Optional callback for progress updates
            
        Returns:
            True if upload successful, False otherwise
        """
        if not self.is_authenticated():
            print("[WCR Cloud] Not authenticated. Call authenticate() first.")
            return False
        
        if not self.can_write:
            print("[WCR Cloud] No write permission for this guild.")
            return False
        
        if not file_path.exists():
            print(f"[WCR Cloud] File not found: {file_path}")
            return False
        
        file_size = file_path.stat().st_size
        
        # Initialize progress tracking
        progress = UploadProgress(
            video_name=metadata.video_name,
            total_bytes=file_size,
            uploaded_bytes=0,
            status='uploading',
            start_time=time.time()
        )
        
        try:
            print(f"[WCR Cloud] Uploading {metadata.video_name} ({file_size / (1024**2):.1f}MB)")
            
            # Check if we need multipart upload
            if file_size > self.MULTIPART_THRESHOLD:
                success = await self._multipart_upload(file_path, metadata, progress, progress_callback)
            else:
                success = await self._single_upload(file_path, metadata, progress, progress_callback)
            
            if success:
                progress.status = 'completed'
                progress.uploaded_bytes = file_size
                if progress_callback:
                    progress_callback(progress)
                print(f"[WCR Cloud] ✅ Upload completed: {metadata.video_name}")
            else:
                progress.status = 'failed'
                if progress_callback:
                    progress_callback(progress)
                print(f"[WCR Cloud] ❌ Upload failed: {metadata.video_name}")
            
            return success
            
        except Exception as e:
            print(f"[WCR Cloud] ❌ Upload error: {e}")
            progress.status = 'failed'
            progress.error = str(e)
            if progress_callback:
                progress_callback(progress)
            return False
    
    async def _single_upload(
        self,
        file_path: Path,
        metadata: VideoMetadata,
        progress: UploadProgress,
        progress_callback: Optional[Callable[[UploadProgress], None]]
    ) -> bool:
        """Upload a file in a single request."""
        try:
            # Step 1: Get presigned upload URL
            guild_encoded = requests.utils.quote(self.guild_name)
            url = f"{self.API_BASE}/guild/{guild_encoded}/video"
            
            # Build metadata payload
            payload = {
                'videoName': metadata.video_name,
                'size': metadata.file_size,
            }
            
            # Add optional metadata
            if metadata.boss_name:
                payload['encounterName'] = metadata.boss_name
            if metadata.difficulty:
                payload['difficulty'] = metadata.difficulty
            if metadata.duration:
                payload['duration'] = metadata.duration
            if metadata.start_time:
                payload['start'] = metadata.start_time
            if metadata.result:
                payload['result'] = metadata.result
            
            # Request presigned URL
            response = requests.post(
                url,
                headers={"Authorization": self.auth_header, "Content-Type": "application/json"},
                json=payload,
                timeout=10
            )
            
            if response.status_code != 200:
                print(f"[WCR Cloud] Failed to get upload URL: {response.status_code}")
                return False
            
            data = response.json()
            upload_url = data.get('url')
            
            if not upload_url:
                print("[WCR Cloud] No upload URL in response")
                return False
            
            # Step 2: Upload file to presigned URL
            with open(file_path, 'rb') as f:
                file_data = f.read()
            
            upload_response = requests.put(
                upload_url,
                data=file_data,
                headers={'Content-Type': 'video/mp4'},
                timeout=300  # 5 minutes for single upload
            )
            
            if upload_response.status_code != 200:
                print(f"[WCR Cloud] Upload failed: {upload_response.status_code}")
                return False
            
            # Update progress
            progress.uploaded_bytes = metadata.file_size
            if progress_callback:
                progress_callback(progress)
            
            return True
            
        except Exception as e:
            print(f"[WCR Cloud] Single upload error: {e}")
            return False
    
    async def _multipart_upload(
        self,
        file_path: Path,
        metadata: VideoMetadata,
        progress: UploadProgress,
        progress_callback: Optional[Callable[[UploadProgress], None]]
    ) -> bool:
        """Upload a large file using multipart upload."""
        try:
            print(f"[WCR Cloud] Using multipart upload (file size: {metadata.file_size / (1024**2):.1f}MB)")
            
            # Step 1: Initiate multipart upload
            guild_encoded = requests.utils.quote(self.guild_name)
            url = f"{self.API_BASE}/guild/{guild_encoded}/video/multipart"
            
            payload = {
                'videoName': metadata.video_name,
                'size': metadata.file_size,
            }
            
            # Add optional metadata
            if metadata.boss_name:
                payload['encounterName'] = metadata.boss_name
            if metadata.difficulty:
                payload['difficulty'] = metadata.difficulty
            if metadata.duration:
                payload['duration'] = metadata.duration
            
            response = requests.post(
                url,
                headers={"Authorization": self.auth_header, "Content-Type": "application/json"},
                json=payload,
                timeout=10
            )
            
            if response.status_code != 200:
                print(f"[WCR Cloud] Failed to initiate multipart upload: {response.status_code}")
                return False
            
            data = response.json()
            upload_id = data.get('uploadId')
            part_urls = data.get('urls', [])
            
            if not upload_id or not part_urls:
                print("[WCR Cloud] Invalid multipart upload response")
                return False
            
            print(f"[WCR Cloud] Multipart upload initiated: {len(part_urls)} parts")
            
            # Step 2: Upload parts
            part_size = self.MULTIPART_THRESHOLD
            parts_info = []
            
            with open(file_path, 'rb') as f:
                for part_num, part_url in enumerate(part_urls, start=1):
                    # Read part data
                    part_data = f.read(part_size)
                    if not part_data:
                        break
                    
                    # Upload part
                    part_response = requests.put(
                        part_url,
                        data=part_data,
                        headers={'Content-Type': 'application/octet-stream'},
                        timeout=300
                    )
                    
                    if part_response.status_code != 200:
                        print(f"[WCR Cloud] Part {part_num} upload failed: {part_response.status_code}")
                        return False
                    
                    # Get ETag from response
                    etag = part_response.headers.get('ETag', '').strip('"')
                    parts_info.append({
                        'PartNumber': part_num,
                        'ETag': etag
                    })
                    
                    # Update progress
                    progress.uploaded_bytes = min(part_num * part_size, metadata.file_size)
                    if progress_callback:
                        progress_callback(progress)
                    
                    print(f"[WCR Cloud] Part {part_num}/{len(part_urls)} uploaded ({progress.progress_percent:.1f}%)")
            
            # Step 3: Complete multipart upload
            complete_url = f"{self.API_BASE}/guild/{guild_encoded}/video/multipart/complete"
            complete_payload = {
                'videoName': metadata.video_name,
                'uploadId': upload_id,
                'parts': parts_info
            }
            
            complete_response = requests.post(
                complete_url,
                headers={"Authorization": self.auth_header, "Content-Type": "application/json"},
                json=complete_payload,
                timeout=30
            )
            
            if complete_response.status_code != 200:
                print(f"[WCR Cloud] Failed to complete multipart upload: {complete_response.status_code}")
                return False
            
            print("[WCR Cloud] ✅ Multipart upload completed")
            return True
            
        except Exception as e:
            print(f"[WCR Cloud] Multipart upload error: {e}")
            return False
    
    async def delete_video(self, video_name: str) -> bool:
        """Delete a video from cloud storage."""
        if not self.can_delete:
            print("[WCR Cloud] No delete permission for this guild.")
            return False
        
        try:
            guild_encoded = requests.utils.quote(self.guild_name)
            video_encoded = requests.utils.quote(video_name)
            url = f"{self.API_BASE}/guild/{guild_encoded}/video/{video_encoded}"
            
            response = requests.delete(
                url,
                headers={"Authorization": self.auth_header},
                timeout=10
            )
            
            if response.status_code == 200:
                print(f"[WCR Cloud] ✅ Deleted: {video_name}")
                return True
            else:
                print(f"[WCR Cloud] ❌ Delete failed: {response.status_code}")
                return False
                
        except Exception as e:
            print(f"[WCR Cloud] Delete error: {e}")
            return False
    
    def is_authenticated(self) -> bool:
        """Check if currently authenticated."""
        return self.authenticated and self.authorized
    
    def get_storage_info(self) -> Dict[str, Any]:
        """Get storage usage and limit information."""
        return {
            'usage_bytes': self.usage_bytes,
            'limit_bytes': self.limit_bytes,
            'usage_gb': round(self.usage_bytes / (1024**3), 2),
            'limit_gb': round(self.limit_bytes / (1024**3), 2),
            'usage_percent': round((self.usage_bytes / self.limit_bytes * 100), 2) if self.limit_bytes > 0 else 0,
            'can_write': self.can_write,
            'can_delete': self.can_delete,
        }


# =============================================================================
# Upload Queue Manager
# =============================================================================

class CloudUploadQueue:
    """
    Manages a queue of videos to upload to cloud storage.
    Supports multiple providers and automatic retry logic.
    """
    
    def __init__(self, provider: CloudUploadProvider):
        """
        Initialize upload queue.
        
        Args:
            provider: Cloud upload provider instance
        """
        self.provider = provider
        self.queue: List[Dict[str, Any]] = []
        self.active_upload: Optional[Dict[str, Any]] = None
        self.completed: List[str] = []
        self.failed: List[Dict[str, Any]] = []
        self.is_running = False
        self.progress_callbacks: List[Callable[[UploadProgress], None]] = []
        
        print(f"[Upload Queue] Initialized with provider: {type(provider).__name__}")
    
    def add_to_queue(self, file_path: Path, metadata: VideoMetadata) -> bool:
        """Add a video to the upload queue."""
        if not file_path.exists():
            print(f"[Upload Queue] ❌ File not found: {file_path}")
            return False
        
        item = {
            'file_path': file_path,
            'metadata': metadata,
            'added_at': time.time(),
            'attempts': 0,
        }
        
        self.queue.append(item)
        print(f"[Upload Queue] Added to queue: {metadata.video_name} (queue size: {len(self.queue)})")
        return True
    
    def add_progress_callback(self, callback: Callable[[UploadProgress], None]):
        """Add a progress callback function."""
        self.progress_callbacks.append(callback)
    
    async def process_queue(self):
        """Process the upload queue."""
        if self.is_running:
            print("[Upload Queue] Already processing queue")
            return
        
        self.is_running = True
        print("[Upload Queue] Starting queue processing")
        
        while self.queue or self.active_upload:
            # Get next item from queue
            if not self.active_upload and self.queue:
                self.active_upload = self.queue.pop(0)
                print(f"[Upload Queue] Processing: {self.active_upload['metadata'].video_name}")
            
            if self.active_upload:
                item = self.active_upload
                item['attempts'] += 1
                
                # Attempt upload
                success = await self.provider.upload_video(
                    item['file_path'],
                    item['metadata'],
                    progress_callback=self._on_progress
                )
                
                if success:
                    self.completed.append(item['metadata'].video_name)
                    print(f"[Upload Queue] ✅ Completed: {item['metadata'].video_name}")
                    self.active_upload = None
                else:
                    # Retry logic (max 3 attempts)
                    if item['attempts'] < 3:
                        print(f"[Upload Queue] ⚠️ Retry {item['attempts']}/3: {item['metadata'].video_name}")
                        self.queue.insert(0, item)  # Put back at front of queue
                        self.active_upload = None
                        await asyncio.sleep(5)  # Wait before retry
                    else:
                        print(f"[Upload Queue] ❌ Failed after 3 attempts: {item['metadata'].video_name}")
                        self.failed.append(item)
                        self.active_upload = None
            else:
                # Queue is empty
                await asyncio.sleep(1)
        
        self.is_running = False
        print("[Upload Queue] Queue processing completed")
        print(f"[Upload Queue] Summary - Completed: {len(self.completed)}, Failed: {len(self.failed)}")
    
    def _on_progress(self, progress: UploadProgress):
        """Internal progress callback that forwards to registered callbacks."""
        for callback in self.progress_callbacks:
            try:
                callback(progress)
            except Exception as e:
                print(f"[Upload Queue] Error in progress callback: {e}")
    
    def get_status(self) -> Dict[str, Any]:
        """Get current queue status."""
        return {
            'is_running': self.is_running,
            'queue_size': len(self.queue),
            'active_upload': self.active_upload['metadata'].video_name if self.active_upload else None,
            'completed_count': len(self.completed),
            'failed_count': len(self.failed),
        }
