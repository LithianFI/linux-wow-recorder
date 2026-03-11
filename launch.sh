#!/bin/bash

# ============================================================================
# WoW Raid Recorder Launcher for Linux/macOS
# ============================================================================

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
cd "$SCRIPT_DIR"

# Function to open browser
open_browser() {
    # Wait 2 seconds for server to start
    sleep 2
    
    # Try to open browser based on OS
    if [[ "$OSTYPE" == "linux-gnu"* ]]; then
        xdg-open http://localhost:5001 &>/dev/null &
    elif [[ "$OSTYPE" == "darwin"* ]]; then
        open http://localhost:5001 &>/dev/null &
    elif [[ "$OSTYPE" == "cygwin" ]] || [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "win32" ]]; then
        start http://localhost:5001 &>/dev/null &
    else
        echo "Note: Please open http://localhost:5001 in your browser"
    fi
}

echo "========================================="
echo "   WoW Raid Recorder Launcher"
echo "========================================="

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
source venv/bin/activate

# Install/update requirements if requirements.txt has changed
REQ_HASH_FILE="venv/.requirements_hash"
CURRENT_HASH=$(md5sum requirements.txt 2>/dev/null || md5 -q requirements.txt 2>/dev/null)
STORED_HASH=$(cat "$REQ_HASH_FILE" 2>/dev/null)

if [ "$CURRENT_HASH" != "$STORED_HASH" ]; then
    echo "Installing/updating requirements..."
    pip install -r requirements.txt
    echo "$CURRENT_HASH" > "$REQ_HASH_FILE"
fi

echo ""
echo "Starting WoW Raid Recorder..."
echo "Web interface: http://localhost:5001"
echo "Press Ctrl+C to stop the application"
echo ""

# Open browser automatically
echo "Opening browser..."
open_browser &

# Run the application
python run.py "$@"
