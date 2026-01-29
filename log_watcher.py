"""
Log Watcher for WoW Raid Recorder.
Monitors WoW log directory and tails new combat log files.
"""

import os
import time
from threading import Event, Thread
from pathlib import Path
from watchdog.events import FileSystemEventHandler

from combat_parser import CombatParser


class LogTailer:
    """Tails a log file and processes new lines."""
    
    def __init__(self, parser: CombatParser):
        self.parser = parser
        self.is_tailing = False
        self._stop_event = Event()
        self._tail_thread: Optional[Thread] = None
    
    def start_tailing(self, log_path: Path) -> bool:
        """Start tailing a log file.
        
        Args:
            log_path: Path to the log file to tail
            
        Returns:
            True if started successfully, False otherwise
        """
        if self.is_tailing:
            print(f"[TAIL] Already tailing a file, stopping first")
            self.stop_tailing()
        
        if not log_path.exists():
            print(f"[TAIL] Log file does not exist: {log_path}")
            return False
        
        print(f"[TAIL] 🎯 Starting to tail: {log_path.name}")
        
        # Reset stop event
        self._stop_event.clear()
        
        # Start tailing thread
        self._tail_thread = Thread(
            target=self._tail_file,
            args=(log_path, self._stop_event),
            daemon=True
        )
        self._tail_thread.start()
        self.is_tailing = True
        
        return True
    
    def stop_tailing(self, timeout: float = 2.0) -> bool:
        """Stop tailing the current log file.
        
        Args:
            timeout: Seconds to wait for thread to stop
            
        Returns:
            True if stopped successfully, False otherwise
        """
        if not self.is_tailing or not self._tail_thread:
            return True
        
        print(f"[TAIL] ⏹️ Stopping tailer...")
        
        # Signal thread to stop
        self._stop_event.set()
        self.is_tailing = False
        
        # Wait for thread to finish
        if self._tail_thread.is_alive():
            self._tail_thread.join(timeout=timeout)
        
        # Check if thread stopped
        if self._tail_thread.is_alive():
            print(f"[TAIL] ⚠️ Tail thread did not stop within {timeout}s")
            return False
        
        print(f"[TAIL] ✅ Tailer stopped")
        self._tail_thread = None
        return True
    
    def _tail_file(self, log_path: Path, stop_event: Event):
        """Thread function to tail a file and process new lines."""
        try:
            with open(log_path, 'r', encoding='utf-8') as file:
                # Start at the end of the file (ignore existing content)
                file.seek(0, os.SEEK_END)
                
                print(f"[TAIL] 📖 Now reading new entries from {log_path.name}")
                
                while not stop_event.is_set():
                    # Read new line
                    line = file.readline()
                    
                    if line:
                        # Process the line
                        self.parser.process_line(line)
                    else:
                        # No new data, sleep briefly
                        time.sleep(0.05)
                        
        except FileNotFoundError:
            print(f"[TAIL] ❌ Log file disappeared: {log_path.name}")
        except Exception as e:
            print(f"[TAIL] ❌ Error tailing file {log_path.name}: {e}")
        finally:
            print(f"[TAIL] 📕 Stopped reading {log_path.name}")
    
    def is_alive(self) -> bool:
        """Check if tailer is currently active."""
        return self.is_tailing and self._tail_thread is not None


class LogDirHandler(FileSystemEventHandler):
    """Watchdog event handler for WoW log directory."""
    
    def __init__(self, parser: CombatParser, log_pattern):
        super().__init__()
        self.parser = parser
        self.log_pattern = log_pattern  # Store the pattern
        self.tailer = LogTailer(parser)
        self.current_log: Optional[Path] = None
    
    def on_created(self, event):
        """Handle new file creation in watched directory."""
        if event.is_directory:
            return
        
        self._handle_new_file(Path(event.src_path))
    
    def on_moved(self, event):
        """Handle file move/rename in watched directory.
        
        WoW often writes to a temp file then renames it to the final log file.
        """
        if event.is_directory:
            return
        
        self._handle_new_file(Path(event.dest_path))
    
    def _handle_new_file(self, file_path: Path):
        """Handle a new or renamed log file."""
        # Use stored pattern instead of creating new config
        if not self.log_pattern.match(file_path.name):
            return
        
        print(f"[WATCHER] 🔍 New combat log detected: {file_path.name}")
        
        # Start tailing the new file
        if self.tailer.start_tailing(file_path):
            self.current_log = file_path
            print(f"[WATCHER] ✅ Now monitoring: {file_path.name}")
        else:
            print(f"[WATCHER] ❌ Failed to start tailing: {file_path.name}")
    
    def attach_to_latest_log(self, log_dir: Path, log_pattern):
        """Attach to the latest existing log file in directory."""
        try:
            # Find all matching log files
            log_files = []
            for file in log_dir.iterdir():
                if log_pattern.match(file.name) and file.is_file():
                    log_files.append(file)
        
            if not log_files:
                print(f"[WATCHER] No existing log files found in {log_dir}")
                return
        
            # Get the most recent log file
            latest_log = max(log_files, key=lambda f: f.stat().st_mtime)
        
            print(f"[WATCHER] Found latest log: {latest_log.name}")
            self._handle_new_file(latest_log)
        
        except Exception as e:
            print(f"[WATCHER] Error finding latest log: {e}")
    
    def stop(self):
        """Stop monitoring and clean up resources."""
        print(f"[WATCHER] 🛑 Stopping log watcher...")
        
        if self.tailer.is_alive():
            self.tailer.stop_tailing()
        
        self.current_log = None
        print(f"[WATCHER] ✅ Log watcher stopped")


class LogMonitor:
    """High-level log monitoring manager."""
    
    def __init__(self, log_dir: Path, parser: CombatParser):
        self.log_dir = log_dir
        self.parser = parser
        self.handler = None
        self.observer = None
    
    def start(self):
        """Start monitoring the log directory."""
        if not self.log_dir.exists():
            raise FileNotFoundError(f"Log directory not found: {self.log_dir}")
        
        print(f"[MONITOR] 📁 Monitoring directory: {self.log_dir}")
        
        # Get log pattern from config (via parser's config)
        log_pattern = self.parser.config.LOG_PATTERN
        
        # Create handler with the pattern
        from watchdog.observers import Observer
        self.handler = LogDirHandler(self.parser, log_pattern)
        self.observer = Observer()
        self.observer.schedule(self.handler, str(self.log_dir), recursive=False)
        self.observer.start()
        
        # Attach to latest existing log
        self.handler.attach_to_latest_log(self.log_dir, log_pattern)
        
        print(f"[MONITOR] ✅ Log monitoring started")
    
    def stop(self):
        """Stop monitoring and clean up."""
        print(f"[MONITOR] 🛑 Stopping log monitor...")
        
        # Stop the handler first
        if self.handler:
            self.handler.stop()
        
        # Stop the observer
        if self.observer:
            try:
                self.observer.stop()
                self.observer.join(timeout=3.0)
            except Exception as e:
                print(f"[MONITOR] ⚠️ Error stopping observer: {e}")
            finally:
                self.observer = None
        
        print(f"[MONITOR] ✅ Log monitor stopped")
    
    def is_monitoring(self) -> bool:
        """Check if monitoring is active."""
        return self.observer is not None and self.observer.is_alive()
    
    def get_status(self) -> dict:
        """Get current monitoring status."""
        return {
            'directory': str(self.log_dir),
            'is_monitoring': self.is_monitoring(),
            'current_log': str(self.handler.current_log) if self.handler.current_log else None,
            'is_tailing': self.handler.tailer.is_alive() if self.handler.tailer else False,
        }


# Optional: Simple function interface for backward compatibility
def create_log_watcher(parser: CombatParser) -> LogDirHandler:
    """Create a log directory handler (backward compatibility)."""
    return LogDirHandler(parser)