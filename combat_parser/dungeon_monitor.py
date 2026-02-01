"""
Dungeon timeout monitoring.
"""

import time
import threading
from typing import Optional, Callable
from datetime import datetime


class DungeonMonitor:
    """Monitors dungeon runs for inactivity timeout."""
    
    def __init__(self, state_manager, config, on_timeout: Optional[Callable] = None):
        self.state = state_manager
        self.config = config
        self.on_timeout = on_timeout
        
        self._monitor_thread = None
        self._running = False
        self._check_interval = 5  # seconds
    
    def start(self):
        """Start the dungeon monitor thread."""
        if self._running:
            return
        
        self._running = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True
        )
        self._monitor_thread.start()
        print("[DUNGEON] Started dungeon timeout monitor")
    
    def stop(self):
        """Stop the dungeon monitor."""
        self._running = False
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=2.0)
        print("[DUNGEON] Stopped dungeon timeout monitor")
    
    def _monitor_loop(self):
        """Monitor dungeon for inactivity timeout."""
        while self._running:
            try:
                if self.state.dungeon_active:
                    timeout = self.config.DUNGEON_TIMEOUT_SECONDS
                    if self.state.is_dungeon_idle(timeout):
                        print(f"[DUNGEON] Dungeon idle for {timeout}s, triggering timeout")
                        if self.on_timeout:
                            self.on_timeout()
                
                time.sleep(self._check_interval)
            except Exception as e:
                print(f"[DUNGEON] Error in monitor: {e}")
                time.sleep(self._check_interval)
    
    def is_running(self) -> bool:
        """Check if monitor is running."""
        return self._running
    
    def get_status(self) -> dict:
        """Get monitor status."""
        return {
            'running': self._running,
            'check_interval': self._check_interval,
            'thread_alive': self._monitor_thread.is_alive() if self._monitor_thread else False,
        }