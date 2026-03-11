@echo off
setlocal

REM ============================================================================
REM WoW Raid Recorder Launcher for Windows
REM ============================================================================

REM Get the directory where this script is located
set SCRIPT_DIR=%~dp0
cd /d "%SCRIPT_DIR%"

echo =========================================
echo    WoW Raid Recorder Launcher
echo =========================================

REM Check if virtual environment exists
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
)

REM Activate virtual environment
call venv\Scripts\activate.bat

REM Install/update requirements if requirements.txt has changed
set REQ_HASH_FILE=venv\.requirements_hash

REM Get current hash of requirements.txt using certutil
for /f "skip=1 tokens=* delims=" %%A in ('certutil -hashfile requirements.txt MD5 2^>nul') do (
    if not defined CURRENT_HASH set CURRENT_HASH=%%A
)

set STORED_HASH=
if exist "%REQ_HASH_FILE%" set /p STORED_HASH=<"%REQ_HASH_FILE%"

if not "%CURRENT_HASH%"=="%STORED_HASH%" (
    echo Installing/updating requirements...
    pip install -r requirements.txt
    echo %CURRENT_HASH%> "%REQ_HASH_FILE%"
)

echo.
echo Starting WoW Raid Recorder...
echo Web interface: http://localhost:5001
echo Press Ctrl+C to stop the application
echo.

REM Open browser automatically after delay
echo Opening browser...
start "" cmd /c "timeout /t 2 /nobreak >nul && start http://localhost:5001"

REM Run the application
python run.py %*
