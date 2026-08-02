#!/bin/bash
# Собрать образ и (пере)запустить бота + планировщик в Docker.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

IMAGE="priem-monitor:latest"
SCHEDULER_INTERVAL="${SCHEDULER_INTERVAL:-7200}"

if [[ ! -f config/telegram.json ]]; then
  echo "Не найден config/telegram.json."
  echo "Создайте его из шаблона и впишите токен бота (@BotFather) + allowed_chat_ids:"
  echo "  cp config/telegram.example.json config/telegram.json"
  exit 1
fi

mkdir -p data/cache logs

echo "Собираю образ $IMAGE..."
docker build -t "$IMAGE" .

VOLUMES=(-v "$ROOT/config:/app/config" -v "$ROOT/data:/app/data" -v "$ROOT/logs:/app/logs")

NAMES=(priem-bot priem-scheduler)
COMMANDS=("python3 scripts/telegram_bot.py" "python3 -u scheduler.py --interval $SCHEDULER_INTERVAL")

for i in "${!NAMES[@]}"; do
  name="${NAMES[$i]}"
  cmd="${COMMANDS[$i]}"
  if [[ -n "$(docker ps -aq -f "name=^${name}\$")" ]]; then
    echo "Пересоздаю контейнер $name..."
    docker rm -f "$name" >/dev/null
  fi
  echo "Запускаю $name: $cmd"
  # shellcheck disable=SC2086
  docker run -d --name "$name" --restart unless-stopped "${VOLUMES[@]}" "$IMAGE" $cmd >/dev/null
done

echo ""
echo "Готово."
echo "Статус:            docker ps --filter name=priem-"
echo "Логи бота:         docker logs -f priem-bot"
echo "Логи планировщика: docker logs -f priem-scheduler"
echo "Остановить:        ./scripts/docker_stop.sh"
