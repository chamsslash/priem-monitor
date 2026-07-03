#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$ROOT/logs"
LOG_FILE="$LOG_DIR/update.log"
LOCK_FILE="$LOG_DIR/update.lock"

mkdir -p "$LOG_DIR"

if [[ -f "$LOCK_FILE" ]]; then
  pid="$(cat "$LOCK_FILE" 2>/dev/null || true)"
  if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
    echo "=== $(date '+%Y-%m-%d %H:%M:%S %z') === skip: update already running (pid $pid)" >> "$LOG_FILE"
    exit 0
  fi
fi

echo "$$" > "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE"' EXIT

{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S %z') ==="
  cd "$ROOT"
  /usr/bin/python3 update.py
  echo
} >> "$LOG_FILE" 2>&1
