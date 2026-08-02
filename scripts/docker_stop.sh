#!/bin/bash
# Остановить и удалить контейнеры бота и планировщика.
set -euo pipefail

docker rm -f priem-bot priem-scheduler 2>/dev/null || true
echo "Контейнеры priem-bot и priem-scheduler остановлены и удалены."
