#!/bin/bash
# ============================================================================
# WoW Raid Recorder — Linux launcher
# Drop this file next to the WoWRaidRecorder binary in the dist folder.
#
# When double-clicked from a file manager the process has no attached TTY,
# so this script detects that and re-spawns itself inside a terminal window.
# When already run from a terminal it executes the binary directly.
# ============================================================================

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
BINARY="$SCRIPT_DIR/WoWRaidRecorder"

if [ ! -f "$BINARY" ]; then
    echo "ERROR: WoWRaidRecorder binary not found at $BINARY"
    exit 1
fi

# ── If we already have a TTY, just run the binary and we're done ─────────────
if [ -t 1 ]; then
    exec "$BINARY" "$@"
fi

# ── No TTY — find a terminal emulator and re-launch inside it ────────────────
# We pass --hold / equivalent flags so the window stays open after the app
# exits (e.g. on a crash) so the user can read the output.

SELF="$(readlink -f "${BASH_SOURCE[0]}")"

# List of (terminal_binary  launch_args_before_command  hold_flag) triples.
# The hold flag keeps the window open after the process exits.
TERMINALS=(
    "gnome-terminal  --  "
    "konsole         --noclose -e "
    "xfce4-terminal  --hold -e "
    "mate-terminal   --  "
    "tilix           --  "
    "xterm           -hold -e "
    "lxterminal      -e "
    "urxvt           -hold -e "
    "kitty           "
    "alacritty       -e "
    "wezterm         start -- "
)

for entry in "${TERMINALS[@]}"; do
    TERM_BIN=$(echo "$entry" | awk '{print $1}')
    TERM_ARGS=$(echo "$entry" | sed "s/^$TERM_BIN[[:space:]]*//" )

    if command -v "$TERM_BIN" &>/dev/null; then
        # shellcheck disable=SC2086
        exec $TERM_BIN $TERM_ARGS "$SELF" "$@"
    fi
done

# ── Absolute fallback: try xterm without hold, then give up ──────────────────
if command -v xterm &>/dev/null; then
    exec xterm -e "$SELF" "$@"
fi

# No terminal found at all — show a desktop notification if possible, then
# just run silently (better than nothing).
MSG="WoW Raid Recorder: no terminal emulator found. Install xterm or gnome-terminal to see log output. The app is running at http://localhost:5001"
if command -v notify-send &>/dev/null; then
    notify-send "WoW Raid Recorder" "$MSG"
fi

exec "$BINARY" "$@"
