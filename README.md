# WoW Raid Recorder

A World of Warcraft encounter recorder for Linux and Mac. Monitors your combat log, automatically starts and stops OBS recordings on boss pulls, and provides a web dashboard for reviewing VODs, clips, and session statistics.

Built with AI assistance. While the code has been reviewed, mistakes happen — if AI-generated code is a concern for you, this project isn't for you.

> **Windows users:** [Warcraft Recorder](https://warcraftrecorder.com/) is a mature, purpose-built tool that does everything this does and more. This project exists specifically for players on systems Warcraft Recorder doesn't support.

---

## What it does

- Detects the latest WoW combat log and watches it in real time
- Automatically starts OBS recording when a boss encounter begins, stops a few seconds after it ends
- Records Mythic+ dungeon runs as a single continuous VOD
- Web dashboard at `http://localhost:5001` with:
  - Live recording status and in-progress pull timer
  - Previous pull summary with death timeline and markers
  - Per-boss progression chart across the session
  - Recent recordings list
  - **Recordings page** — full library with search, filters, and sort
  - **Clips page** — manage exported highlight clips (rename, delete, download)
  - **Statistics page** — per-boss breakdown across all recordings: pull count, kill rate, best/average time, boss HP% progression on wipes, death leaderboard
- Video player with timeline scrubbing, death markers, volume control, playback speed (0.5×–2×), fullscreen, and keyboard shortcuts
- Clip export (requires ffmpeg — see below)
- Optional cloud upload (Google Drive)

---

## Requirements

### Required

- **Python 3.10+**
- **OBS Studio** with the WebSocket server enabled
  - In OBS: Tools → WebSocket Server Settings → Enable WebSocket Server
- **A WoW combat log** — enable it in-game: Main Menu → Options → Network → Advanced Combat Logging

### Optional but recommended

- **ffmpeg** — required for the clip export feature (cutting highlights from recordings)

#### Installing ffmpeg

**Ubuntu / Debian**
```bash
sudo apt update && sudo apt install ffmpeg
```

**Fedora**
```bash
sudo dnf install ffmpeg
```

**Arch / Manjaro**
```bash
sudo pacman -S ffmpeg
```

**macOS (Homebrew)**
```bash
brew install ffmpeg
```

**Windows**
Download a build from [ffmpeg.org](https://ffmpeg.org/download.html) and add the `bin/` folder to your system PATH. Or use a package manager:
```powershell
winget install ffmpeg
# or
choco install ffmpeg
```

Verify the install worked:
```bash
ffmpeg -version
```

---

## Running from source

### Linux / macOS

```bash
# Make the launcher executable (first time only)
chmod +x launch.sh

# Start the app
./launch.sh
```

The launcher creates a virtual environment, installs dependencies, and opens your browser automatically. Use `--no-browser` to skip the browser open.

### Windows

Double-click `launch.bat`.

### Command line options

```
--config PATH    Path to config file (default: config.ini)
--host HOST      Web server host (default: 0.0.0.0)
--port PORT      Web server port (default: 5001)
--no-recorder    Start web GUI only, without recorder
--debug          Enable debug mode
```

---

## Running the pre-built executable (Linux)

A pre-built binary requires no Python installation.

1. Extract the archive:
   ```bash
   tar -xzf WoWRaidRecorder-linux-x86_64.tar.gz
   cd WoWRaidRecorder
   ```

2. Make the launcher executable (first time only):
   ```bash
   chmod +x WoWRaidRecorder.sh
   ```

3. Run via the launcher script — not the binary directly:
   ```bash
   ./WoWRaidRecorder.sh
   ```

   The script detects whether it's running in a terminal. If you double-click it from a file manager it will automatically open a terminal window (gnome-terminal, konsole, xfce4-terminal, xterm, kitty, alacritty, and others are supported). The terminal stays open so you can see log output and spot any errors.

4. On first run, a `config.ini` will need to be created. The app will guide you through this via the web interface at `http://localhost:5001`. A `config.ini.example` is included as a reference.

> **Note:** The binary is built against a specific glibc version. If you get a glibc error on an older distribution, run from source instead.

---

## Building the executable yourself

You need to build on Linux to produce a Linux binary.

```bash
# Activate your virtual environment
source venv/bin/activate

# Install PyInstaller
pip install pyinstaller

# Build
pyinstaller wow_raid_recorder.spec
```

The distributable output lands in `dist/WoWRaidRecorder/`. Package it:

```bash
cd dist
tar -czf WoWRaidRecorder-linux-x86_64.tar.gz WoWRaidRecorder/
```

> ffmpeg is **not** bundled in the executable — it must be installed separately on the target machine if clip export is needed.

---

## Configuration

On first run the app starts without a recorder and opens the configuration page. The main things to set:

- **WoW log directory** — the folder containing `WoWCombatLog.txt`, typically inside your WoW installation under `_retail_/Logs/`
- **OBS connection** — host (usually `localhost`), port (default `4455`), and password if you set one
- **Recording output path** — where OBS saves recordings; must match the path set in OBS

---

## Contributing

PRs and issues welcome. The project is intentionally simple — a thin Python backend talking to OBS over WebSocket and serving a vanilla HTML/Alpine.js frontend. No build step required for the web UI.
