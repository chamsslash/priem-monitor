#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.dima.priem-telegram-bot"
PLIST_DEST="$HOME/Library/LaunchAgents/${LABEL}.plist"
PYTHON="/usr/bin/python3"
BOT_SCRIPT="$ROOT/scripts/telegram_bot.py"
CONFIG="$ROOT/config/telegram.json"

if [[ ! -f "$CONFIG" ]]; then
  echo "Сначала создайте config/telegram.json"
  echo "Шаблон: $ROOT/config/telegram.example.json"
  exit 1
fi

chmod +x "$BOT_SCRIPT"

mkdir -p "$HOME/Library/LaunchAgents" "$ROOT/logs"

cat > "$PLIST_DEST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${PYTHON}</string>
    <string>${BOT_SCRIPT}</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${ROOT}/logs/telegram_bot.out.log</string>
  <key>StandardErrorPath</key>
  <string>${ROOT}/logs/telegram_bot.err.log</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_DEST"
launchctl enable "gui/$(id -u)/${LABEL}"

echo "Telegram-бот установлен."
echo "  Plist: $PLIST_DEST"
echo "  Лог: $ROOT/logs/telegram_bot.log"
echo ""
echo "Проверка: launchctl print gui/$(id -u)/${LABEL}"
echo "Снять: $ROOT/scripts/uninstall_telegram_bot.sh"
