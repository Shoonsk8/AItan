#!/bin/bash
# Fast launcher for AISearch — if an instance is already running (detected
# via flock on the lock file), append our file args to the drop file and
# exit immediately without paying Python's import cost. Otherwise exec the
# Python app to start fresh.
#
# Used by the .desktop file's Exec= line so right-click → Open with AISearch
# gets near-instant turnaround when an instance is already up.

LOCK_FILE="/tmp/aisearch-$(id -u).lock"
DROP_FILE="/tmp/aisearch-$(id -u).drop"
APP_DIR="/mnt/1TBSSD/AIsearch"

# Try to acquire the lock non-blocking via a separate fd. If it fails, the
# Python instance is holding it — we're a "secondary" launch.
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    # Primary instance running — append our file args to the drop file and exit.
    if [ "$#" -gt 0 ]; then
        for arg in "$@"; do
            if [ -e "$arg" ]; then
                # Use flock for the drop file to serialize concurrent appends
                ( flock 8; printf '%s\n' "$arg" >> "$DROP_FILE" ) 8>>"$DROP_FILE"
            fi
        done
    fi
    exit 0
fi

# Release our test lock; Python will re-acquire it.
flock -u 9
exec 9>&-

# No primary instance — start the Python app, passing through any args.
exec "$APP_DIR/venv/bin/python" "$APP_DIR/aisearch_main.py" "$@"
