#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INTERVAL="${1:-7200}"
LABEL="com.dima.priem-monitor"
PLIST_DEST="$HOME/Library/LaunchAgents/${LABEL}.plist"
PYTHON="/usr/bin/python3"
UPDATE_SCRIPT="$ROOT/scripts/scheduled_update.py"

if ! [[ "$INTERVAL" =~ ^[0-9]+$ ]] || [[ "$INTERVAL" -lt 300 ]]; then
  echo "Интервал должен быть числом секунд, не меньше 300 (5 минут)."
  echo "Пример: $0 7200   # каждые 2 часа"
  exit 1
fi

chmod +x "$UPDATE_SCRIPT" "$ROOT/scripts/run_update.sh"

mkdir -p "$HOME/Library/LaunchAgents" "$ROOT/logs"

if [[ "$ROOT" == *"/Desktop/"* ]] || [[ "$ROOT" == *"/Documents/"* ]] || [[ "$ROOT" == *"/Downloads/"* ]]; then
  echo "Внимание: проект в защищённой папке macOS (Desktop/Documents/Downloads)."
  echo "LaunchAgent может не получить доступ без разрешения в"
  echo "Системные настройки -> Конфиденциальность -> Полный доступ к диску."
  echo "Надёжнее перенести проект, например, в ~/Projects/Парсинг списков"
  echo ""
fi

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
    <string>${UPDATE_SCRIPT}</string>
  </array>
  <key>StartInterval</key>
  <integer>${INTERVAL}</integer>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${ROOT}/logs/launchd.out.log</string>
  <key>StandardErrorPath</key>
  <string>${ROOT}/logs/launchd.err.log</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_DEST"
launchctl enable "gui/$(id -u)/${LABEL}"

hours=$((INTERVAL / 3600))
minutes=$(((INTERVAL % 3600) / 60))
echo "Автообновление установлено."
echo "  Интервал: ${INTERVAL} сек (~${hours}ч ${minutes}м)"
echo "  Plist: $PLIST_DEST"
echo "  Лог обновлений: $ROOT/logs/update.log"
echo ""
echo "Проверка: launchctl print gui/$(id -u)/${LABEL}"
echo "Снять: $ROOT/scripts/uninstall_scheduler.sh"
