# log_watcher.py
import os
import time
from threading import Event, Thread
from pathlib import Path
from watchdog.events import FileSystemEventHandler

from config import Config
from combat_parser import CombatParser

class LogTailer:
    def __init__(self, parser: CombatParser):
        self.parser = parser
        
    def tail_file(self, path: Path, stop_event: Event):
        """Tail a log file and process each new line"""
        print(f"[TAIL] Watching {path.name}")
        
        with path.open("r", encoding="utf-8") as f:
            f.seek(0, os.SEEK_END)  # ignore historic lines
            
            while not stop_event.is_set():
                line = f.readline()
                if not line:
                    time.sleep(0.05)
                    continue
                self.parser.process_line(line)

class LogDirHandler(FileSystemEventHandler):
    def __init__(self, parser: CombatParser):
        super().__init__()
        self.parser = parser
        self.tailer = LogTailer(parser)
        self.tailer_thread = None
        self.stop_evt = None

    def _start_new_tail(self, new_path: Path):
        """Start tailing a new log file, stopping previous tailer if exists"""
        # Stop any previous tailer first
        if self.stop_evt and self.tailer_thread:
            self.stop_evt.set()
            if self.tailer_thread.is_alive():
                self.tailer_thread.join(timeout=2)

        # Start new tailer
        self.stop_evt = Event()
        self.tailer_thread = Thread(
            target=self.tailer.tail_file, 
            args=(new_path, self.stop_evt), 
            daemon=True
        )
        self.tailer_thread.start()
        print(f"[WATCHER] Now tailing: {new_path.name}")

    def on_created(self, event):
        """Handle new file creation"""
        if event.is_directory:
            return
        fname = os.path.basename(event.src_path)
        if Config.LOG_PATTERN.match(fname):
            print(f"[WATCHER] New combat-log detected: {fname}")
            self._start_new_tail(Path(event.src_path))

    def on_moved(self, event):
        """Handle file move/rename (common in WoW logging)"""
        self.on_created(event)
    
    def stop(self):
        """Stop the current tailer"""
        if self.stop_evt:
            self.stop_evt.set()
        if self.tailer_thread and self.tailer_thread.is_alive():
            self.tailer_thread.join(timeout=2)