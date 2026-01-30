Very basic World of Warcraft encounter recorder using OBS.

AI used extensively to get this thing up and running. While I have read through the code to hopefully avoid big caveats, mistakes happen. If use of generative AI is an issue for you, this isn't a tool for you.

What currently works:
Software detects the latest combatlog file and monitors it. When boss encounter starts it will tell OBS to start recording. Once the encounte ends, the recording continues for 3 more seconds before stop.
What needs to be done:

- ~~Config~~
- ~~Frontend~~
- Maybe attempt to integrate with WarcraftRecorder so that the replays are send to the cloud storage there

I will work on this slowly, if anyone wants to contribute/fork/whatever they are welcome to.

Tested on Linux and Mac, should work on Windows as well, but if you are on Windows you really should use https://warcraftrecorder.com/ , it is better than this is ever going to be in every way possible. This is made just to have something functional on systems that don't support Warcraft Recorder.

## How to run

1. Create and activate a virtual environment:
```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Copy and configure your config file:
```bash
cp config.ini.example config.ini
```
   Edit `config.ini` with your settings:
   - Set `log_dir` to your WoW Logs directory
   - Configure OBS WebSocket settings (host, port, password)
   - Set `recording_path_fallback` to your OBS recording directory

4. Make sure OBS is running with WebSocket server enabled (Tools > WebSocket Server Settings)

5. Run the application:
```bash
python run.py
```

6. Open http://localhost:5001 in your browser

### Command line options

```
--config PATH    Path to config file (default: config.ini)
--host HOST      Web server host (default: 0.0.0.0)
--port PORT      Web server port (default: 5001)
--no-recorder    Start web GUI only, without recorder
--debug          Enable debug mode
```
