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

First, make sure you have OBS installed and configured. You also need to enable Websocket Server in OBS. This is found under Tools -> Websocket Server Settings -> Enable Websocket server

Linx/Mac

1. Make the script executable via terminal: chmod +x launch.sh (Make sure you are in the folder where the app is located)
2. ./launch.sh

Windows:

1. Double-click on launch.bat

Optionally:

Open http://localhost:5001 in your browser to access the GUI if it didn't automatically load

### Command line options

```
--config PATH    Path to config file (default: config.ini)
--host HOST      Web server host (default: 0.0.0.0)
--port PORT      Web server port (default: 5001)
--no-recorder    Start web GUI only, without recorder
--debug          Enable debug mode
```
