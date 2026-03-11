# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec file for WoW Raid Recorder
# Build with: pyinstaller wow_raid_recorder.spec

import sys
from pathlib import Path

block_cipher = None

a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=[],
    datas=[
        # Bundle HTML templates and static assets
        ('templates', 'templates'),
        #('static', 'static'),
        # Bundle example config
        ('config.ini.example', '.'),
    ],
    hiddenimports=[
        # Flask + SocketIO internals that PyInstaller misses
        'flask',
        'flask_socketio',
        'engineio',
        'socketio',
        'engineio.async_drivers.threading',
        'socketio.async_drivers.threading',
        # Watchdog platform backends
        'watchdog.observers',
        'watchdog.observers.polling',
        'watchdog.events',
        # OBS websocket
        'obsws_python',
        # Google API (optional cloud upload)
        'google.auth',
        'google.auth.transport.requests',
        'google_auth_oauthlib.flow',
        'googleapiclient.discovery',
        'googleapiclient.http',
        # Standard lib helpers
        'configparser',
        'asyncio',
        'threading',
        'signal',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='WoWRaidRecorder',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,   # Keep console window so users can see log output
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # Windows only: show a nice icon if you have one
    # icon='assets/icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='WoWRaidRecorder',
)
