#!/bin/bash
# Остановить и удалить контейнер бота (+ устаревший планировщик, если остался).
set -euo pipefail

docker rm -f priem-bot priem-scheduler 2>/dev/null || true
echo "Контейнер priem-bot остановлен и удалён (и устаревший priem-scheduler, если был)."
