"""
Cloud Upload Module for WoW Raid Recorder.
Provides automated upload to cloud storage services after recording completion.
"""

import os
import time
import base64
import asyncio
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Dict, Any, List, Callable
from dataclasses import dataclass, asdict
from datetime import datetime

import requests


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
        if self.total_bytes == 0:
            return 0.0
        return (self.uploaded_bytes / self.total_bytes) * 100

    @property
    def upload_speed(self) -> float:
        elapsed = time.time() - self.start_time
        if elapsed == 0:
            return 0.0
        return self.uploaded_bytes / elapsed


@dataclass
class VideoMetadata:
    """Metadata for uploaded videos — matches WCR CloudMetadata / Metadata shape exactly."""
    video_name: str       # stem without extension
    video_key: str        # filename with extension, exactly as stored in R2
    file_path: str
    file_size: int
    start: int            # epoch ms
    unique_hash: str
    category: str = 'Raids'          # must be a VideoCategory string: 'Raids', 'Mythic+', etc.
    flavour: str = 'Retail'
    encounter_name: Optional[str] = None
    encounter_id: Optional[int] = None
    difficulty_id: Optional[int] = None
    difficulty: Optional[str] = None
    duration: Optional[int] = None
    result: bool = False
    boss_percent: int = 0
    zone_id: int = 0
    zone_name: str = 'Unknown'
    player: Optional[Dict] = None
    combatants: Optional[List] = None
    deaths: Optional[List] = None
    overrun: int = 0
    app_version: str = '1.0.0'
    # M+ specific fields (maps to ChallengeModeDungeon.getMetadata())
    keystone_level: Optional[int] = None
    map_id: Optional[int] = None
    upgrade_level: int = 0
    affixes: Optional[List[int]] = None

    def to_cloud_metadata(self) -> dict:
        """Build the payload for POST /guild/{g}/video matching WCR CloudMetadata type."""
        d = {
            'videoName': self.video_name,
            'videoKey': self.video_key,
            'start': self.start,
            'uniqueHash': self.unique_hash,
            'category': self.category,
            'flavour': self.flavour,
            'duration': self.duration or 0,
            'result': self.result,
            'bossPercent': self.boss_percent,
            'zoneID': self.zone_id,
            'zoneName': self.zone_name,
            'overrun': self.overrun,
            'combatants': self.combatants or [],
            'appVersion': self.app_version,
        }

        if self.encounter_name:
            d['encounterName'] = self.encounter_name
        if self.encounter_id:
            d['encounterID'] = self.encounter_id
        if self.difficulty_id:
            d['difficultyID'] = self.difficulty_id
        if self.difficulty:
            d['difficulty'] = self.difficulty
        if self.player:
            d['player'] = self.player
        if self.deaths:
            d['deaths'] = self.deaths

        # M+ specific fields — only include when present
        if self.keystone_level is not None:
            d['keystoneLevel'] = self.keystone_level
        if self.map_id is not None:
            d['mapID'] = self.map_id
        if self.upgrade_level:
            d['upgradeLevel'] = self.upgrade_level
        if self.affixes:
            d['affixes'] = self.affixes

        return d

# =============================================================================
# Abstract Base
# =============================================================================

class CloudUploadProvider(ABC):
    @abstractmethod
    async def authenticate(self) -> bool:
        pass

    @abstractmethod
    async def upload_video(self, file_path: Path, metadata: VideoMetadata,
                           progress_callback=None) -> bool:
        pass

    @abstractmethod
    def is_authenticated(self) -> bool:
        pass

    @abstractmethod
    def get_storage_info(self) -> Dict[str, Any]:
        pass


# =============================================================================
# Warcraft Recorder Cloud Provider
# =============================================================================

class WarcraftRecorderCloud(CloudUploadProvider):
    """
    Upload provider matching the WCR CloudClient.ts implementation exactly.

    Upload flow (single part, file < 100 MB):
      1. POST /guild/{g}/upload          {key, bytes}  → {signed: <put_url>}
      2. PUT  <signed_url>               raw file bytes
      3. POST /guild/{g}/video           CloudMetadata payload

    Upload flow (multipart, file >= 100 MB):
      1. POST /guild/{g}/create-multipart-upload   {key, total, part}  → {urls: [...]}
      2. PUT  urls[0..n]                            file chunks
      3. POST /guild/{g}/complete-multipart-upload  {key, etags: [...]}
      4. POST /guild/{g}/video                      CloudMetadata payload

    Authentication:
      GET /user/affiliations  → list of {guildName, read, write, del}
    """

    API_BASE = 'https://api.warcraftrecorder.com/api'
    MULTIPART_THRESHOLD = 100 * 1024 * 1024  # 100 MB

    def __init__(self, username: str, password: str, guild_name: str):
        self.username = username
        self.password = password
        self.guild_name = guild_name

        self.authenticated = False
        self.can_write = False
        self.can_read = False
        self.can_delete = False
        self.usage_bytes = 0
        self.limit_bytes = 0

        raw = f"{username}:{password}".encode('utf-8')
        self.auth_header = f"Basic {base64.b64encode(raw).decode('utf-8')}"
        self._headers = {'Authorization': self.auth_header}

        print(f"[WCR Cloud] Initialized for guild: {guild_name}")

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    async def authenticate(self) -> bool:
        """
        Authenticate by fetching /user/affiliations and checking guild membership.
        Mirrors CloudClient.configure() / fetchAffiliations().
        """
        try:
            url = f"{self.API_BASE}/user/affiliations"
            resp = requests.get(url, headers=self._headers, timeout=10)

            if resp.status_code == 401:
                print("[WCR Cloud] ❌ Invalid credentials (401)")
                return False

            resp.raise_for_status()
            affiliations = resp.json()  # [{guildName, read, write, del}, ...]

            print(f"[WCR Cloud] Got {len(affiliations)} affiliations")

            affiliation = next(
                (a for a in affiliations if a.get('guildName') == self.guild_name),
                None
            )

            if not affiliation:
                print(f"[WCR Cloud] ❌ Not affiliated with guild: {self.guild_name}")
                return False

            self.can_read = affiliation.get('read', False)
            self.can_write = affiliation.get('write', False)
            self.can_delete = affiliation.get('del', False)

            if not self.can_write:
                print(f"[WCR Cloud] ❌ No write permission for guild: {self.guild_name}")
                return False

            self.authenticated = True
            print(f"[WCR Cloud] ✅ Authenticated. write={self.can_write}")

            # Fetch storage info (non-fatal if it fails)
            try:
                self._fetch_storage_info()
            except Exception as e:
                print(f"[WCR Cloud] Warning: could not fetch storage info: {e}")

            return True

        except Exception as e:
            print(f"[WCR Cloud] ❌ Authentication error: {e}")
            return False

    def _fetch_storage_info(self):
        g = requests.utils.quote(self.guild_name)
        usage = requests.get(f"{self.API_BASE}/guild/{g}/usage",
                             headers=self._headers, timeout=10)
        if usage.status_code == 200:
            self.usage_bytes = usage.json().get('bytes', 0)

        limit = requests.get(f"{self.API_BASE}/guild/{g}/limit",
                             headers=self._headers, timeout=10)
        if limit.status_code == 200:
            self.limit_bytes = limit.json().get('bytes', 0)

        print(f"[WCR Cloud] Storage: "
              f"{self.usage_bytes / (1024**3):.2f} GB / "
              f"{self.limit_bytes / (1024**3):.2f} GB")

    def is_authenticated(self) -> bool:
        return self.authenticated and self.can_write

    def get_storage_info(self) -> Dict[str, Any]:
        return {
            'usage_bytes': self.usage_bytes,
            'limit_bytes': self.limit_bytes,
            'usage_gb': round(self.usage_bytes / (1024**3), 2),
            'limit_gb': round(self.limit_bytes / (1024**3), 2),
            'usage_percent': round(self.usage_bytes / self.limit_bytes * 100, 2)
                             if self.limit_bytes else 0,
            'can_write': self.can_write,
            'can_delete': self.can_delete,
        }

    # ------------------------------------------------------------------
    # Upload entry point
    # ------------------------------------------------------------------

    async def upload_video(self, file_path: Path, metadata: VideoMetadata,
                           progress_callback=None) -> bool:
        if not self.is_authenticated():
            print("[WCR Cloud] Not authenticated")
            return False

        if not file_path.exists():
            print(f"[WCR Cloud] File not found: {file_path}")
            return False

        file_size = file_path.stat().st_size
        progress = UploadProgress(
            video_name=metadata.video_key,
            total_bytes=file_size,
            uploaded_bytes=0,
            status='uploading',
            start_time=time.time(),
        )

        try:
            print(f"[WCR Cloud] Uploading {metadata.video_key} "
                  f"({file_size / (1024**2):.1f} MB)")

            if file_size >= self.MULTIPART_THRESHOLD:
                success = await self._multipart_upload(
                    file_path, metadata, progress, progress_callback)
            else:
                success = await self._single_upload(
                    file_path, metadata, progress, progress_callback)

            if success:
                # Step 3 (both paths): register metadata in WCR database
                await self._post_video_metadata(metadata)
                progress.status = 'completed'
                progress.uploaded_bytes = file_size
                if progress_callback:
                    progress_callback(progress)
                print(f"[WCR Cloud] ✅ Upload complete: {metadata.video_key}")
            else:
                progress.status = 'failed'
                if progress_callback:
                    progress_callback(progress)

            return success

        except Exception as e:
            print(f"[WCR Cloud] ❌ Upload error: {e}")
            progress.status = 'failed'
            progress.error = str(e)
            if progress_callback:
                progress_callback(progress)
            return False

    # ------------------------------------------------------------------
    # Single-part upload
    # ------------------------------------------------------------------

    async def _single_upload(self, file_path: Path, metadata: VideoMetadata,
                              progress: UploadProgress, progress_callback) -> bool:
        """
        Step 1: POST /guild/{g}/upload  {key, bytes}  → {signed: url}
        Step 2: PUT signed_url  <file bytes>
        """
        try:
            key = metadata.video_key
            file_size = file_path.stat().st_size
            g = requests.utils.quote(self.guild_name)

            # Step 1 – get signed PUT URL
            sign_resp = requests.post(
                f"{self.API_BASE}/guild/{g}/upload",
                headers={**self._headers, 'Content-Type': 'application/json'},
                json={'key': key, 'bytes': file_size},
                timeout=10,
            )

            if sign_resp.status_code != 200:
                print(f"[WCR Cloud] Failed to get signed URL: {sign_resp.status_code} {sign_resp.text}")
                return False

            signed_url = sign_resp.json().get('signed')
            if not signed_url:
                print("[WCR Cloud] No signed URL in response")
                return False

            # Step 2 – PUT file to signed URL
            content_type = 'video/mp4' if key.endswith('.mp4') else 'application/octet-stream'

            with open(file_path, 'rb') as f:
                put_resp = requests.put(
                    signed_url,
                    data=f,
                    headers={
                        'Content-Type': content_type,
                        'Content-Length': str(file_size),
                    },
                    timeout=600,
                )

            if put_resp.status_code not in (200, 204):
                print(f"[WCR Cloud] PUT failed: {put_resp.status_code}")
                return False

            progress.uploaded_bytes = file_size
            if progress_callback:
                progress_callback(progress)

            return True

        except Exception as e:
            print(f"[WCR Cloud] Single upload error: {e}")
            return False

    # ------------------------------------------------------------------
    # Multipart upload
    # ------------------------------------------------------------------

    async def _multipart_upload(self, file_path: Path, metadata: VideoMetadata,
                                 progress: UploadProgress, progress_callback) -> bool:
        """
        Step 1: POST /guild/{g}/create-multipart-upload  {key, total, part}  → {urls:[...]}
        Step 2: PUT each url with its chunk, collect etags
        Step 3: POST /guild/{g}/complete-multipart-upload  {key, etags:[...]}
        """
        try:
            key = metadata.video_key
            file_size = file_path.stat().st_size
            g = requests.utils.quote(self.guild_name)
            part_size = self.MULTIPART_THRESHOLD

            # Step 1 – create multipart upload, get signed part URLs
            create_resp = requests.post(
                f"{self.API_BASE}/guild/{g}/create-multipart-upload",
                headers={**self._headers, 'Content-Type': 'application/json'},
                json={'key': key, 'total': file_size, 'part': part_size},
                timeout=10,
            )

            if create_resp.status_code != 200:
                print(f"[WCR Cloud] Failed to create multipart upload: "
                      f"{create_resp.status_code} {create_resp.text}")
                return False

            urls: List[str] = create_resp.json().get('urls', [])
            if not urls:
                print("[WCR Cloud] No part URLs in multipart response")
                return False

            print(f"[WCR Cloud] Multipart upload: {len(urls)} parts")

            # Step 2 – upload each part
            etags: List[str] = []
            content_type = 'video/mp4' if key.endswith('.mp4') else 'application/octet-stream'
            offset = 0

            with open(file_path, 'rb') as f:
                for part_num, url in enumerate(urls, start=1):
                    remaining = file_size - offset
                    chunk_size = min(part_size, remaining)
                    chunk = f.read(chunk_size)

                    if not chunk:
                        break

                    put_resp = requests.put(
                        url,
                        data=chunk,
                        headers={
                            'Content-Type': content_type,
                            'Content-Length': str(len(chunk)),
                        },
                        timeout=600,
                    )

                    if put_resp.status_code not in (200, 204):
                        print(f"[WCR Cloud] Part {part_num} failed: {put_resp.status_code}")
                        return False

                    # Strip quotes from etag (axios does this too per WCR source)
                    etag = put_resp.headers.get('ETag', '').replace('"', '')
                    etags.append(etag)

                    offset += chunk_size
                    progress.uploaded_bytes = offset
                    if progress_callback:
                        progress_callback(progress)

                    pct = round(100 * offset / file_size, 1)
                    print(f"[WCR Cloud] Part {part_num}/{len(urls)} done ({pct}%)")

            # Step 3 – complete multipart upload
            complete_resp = requests.post(
                f"{self.API_BASE}/guild/{g}/complete-multipart-upload",
                headers={**self._headers, 'Content-Type': 'application/json'},
                json={'key': key, 'etags': etags},  # flat string list, no part numbers
                timeout=30,
            )

            if complete_resp.status_code not in (200, 204):
                print(f"[WCR Cloud] Failed to complete multipart: "
                      f"{complete_resp.status_code} {complete_resp.text}")
                return False

            print("[WCR Cloud] Multipart upload complete")
            return True

        except Exception as e:
            print(f"[WCR Cloud] Multipart upload error: {e}")
            return False

    # ------------------------------------------------------------------
    # Post metadata to WCR database (always last step after file upload)
    # ------------------------------------------------------------------

    async def _post_video_metadata(self, metadata: VideoMetadata):
        """POST /guild/{g}/video  with CloudMetadata payload."""
        try:
            g = requests.utils.quote(self.guild_name)
            payload = metadata.to_cloud_metadata()

            resp = requests.post(
                f"{self.API_BASE}/guild/{g}/video",
                headers={**self._headers, 'Content-Type': 'application/json'},
                json=payload,
                timeout=15,
            )

            if resp.status_code not in (200, 201, 204):
                print(f"[WCR Cloud] Failed to post metadata: {resp.status_code} {resp.text}")
            else:
                print(f"[WCR Cloud] ✅ Metadata registered: {metadata.video_key}")

        except Exception as e:
            print(f"[WCR Cloud] Error posting metadata: {e}")


# =============================================================================
# Upload Queue — persistent worker, never exits until stop() is called
# =============================================================================

class CloudUploadQueue:
    """
    Persistent upload queue. The worker thread runs continuously and blocks
    on an Event when idle, so items added after startup are always picked up.
    """

    def __init__(self, provider: CloudUploadProvider):
        self.provider = provider
        self._queue: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._work_event = threading.Event()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._active: Optional[Dict] = None
        self.completed: List[str] = []
        self.failed: List[Dict] = []
        self._progress_callbacks: List[Callable] = []
        print(f"[Upload Queue] Initialized ({type(provider).__name__})")

    def start(self):
        """Start the persistent worker thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()
        print("[Upload Queue] Worker started")

    def stop(self):
        """Signal the worker to stop after the current upload finishes."""
        self._stop_event.set()
        self._work_event.set()  # wake it up so it can exit

    def add_to_queue(self, file_path: Path, metadata: VideoMetadata) -> bool:
        if not file_path.exists():
            print(f"[Upload Queue] ❌ File not found: {file_path}")
            return False

        item = {
            'file_path': file_path,
            'metadata': metadata,
            'added_at': time.time(),
            'attempts': 0,
        }

        with self._lock:
            self._queue.append(item)

        print(f"[Upload Queue] Queued: {metadata.video_key} "
              f"(queue size: {len(self._queue)})")

        # Wake the worker thread
        self._work_event.set()
        return True

    def add_progress_callback(self, cb: Callable):
        self._progress_callbacks.append(cb)

    def _on_progress(self, progress: UploadProgress):
        for cb in self._progress_callbacks:
            try:
                cb(progress)
            except Exception as e:
                print(f"[Upload Queue] Progress callback error: {e}")

    def _worker(self):
        """Persistent worker loop — blocks when idle, wakes on new items."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        while not self._stop_event.is_set():
            # Block until there's something to do
            self._work_event.wait()
            self._work_event.clear()

            while True:
                with self._lock:
                    if not self._queue:
                        break
                    item = self._queue.pop(0)

                item['attempts'] += 1
                self._active = item
                meta = item['metadata']

                print(f"[Upload Queue] Processing: {meta.video_key} "
                      f"(attempt {item['attempts']})")

                try:
                    success = loop.run_until_complete(
                        self.provider.upload_video(
                            item['file_path'], meta,
                            progress_callback=self._on_progress,
                        )
                    )
                except Exception as e:
                    print(f"[Upload Queue] Exception during upload: {e}")
                    success = False

                if success:
                    self.completed.append(meta.video_key)
                    print(f"[Upload Queue] ✅ Completed: {meta.video_key}")
                elif item['attempts'] < 3:
                    print(f"[Upload Queue] ⚠️ Retry {item['attempts']}/3: {meta.video_key}")
                    with self._lock:
                        self._queue.insert(0, item)  # retry next iteration
                    time.sleep(5)
                else:
                    print(f"[Upload Queue] ❌ Failed after 3 attempts: {meta.video_key}")
                    self.failed.append(item)

                self._active = None

        loop.close()
        print("[Upload Queue] Worker stopped")

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            q_size = len(self._queue)
        return {
            'is_running': self._thread.is_alive() if self._thread else False,
            'queue_size': q_size,
            'active_upload': self._active['metadata'].video_key if self._active else None,
            'completed_count': len(self.completed),
            'failed_count': len(self.failed),
        }
