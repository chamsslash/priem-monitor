#!/bin/bash
set -euo pipefail

LABEL="com.dima.priem-monitor"
PLIST_DEST="$HOME/Library/LaunchAgents/${LABEL}.plist"

launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
rm -f "$PLIST_DEST"

echo "Автообновление отключено."
