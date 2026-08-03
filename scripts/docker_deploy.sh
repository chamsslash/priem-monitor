#!/bin/bash
# Собрать образ и (пере)запустить бота в Docker.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

IMAGE="priem-monitor:latest"

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

# Устаревший контейнер планировщика: дашборд снесён, робот греется внутри бота
# (см. _robot_pool_refresh_loop). Убираем, если остался с прошлых деплоев.
if [[ -n "$(docker ps -aq -f "name=^priem-scheduler\$")" ]]; then
  echo "Удаляю устаревший контейнер priem-scheduler..."
  docker rm -f priem-scheduler >/dev/null
fi

name=priem-bot
cmd="python3 scripts/telegram_bot.py"
if [[ -n "$(docker ps -aq -f "name=^${name}\$")" ]]; then
  echo "Пересоздаю контейнер $name..."
  docker rm -f "$name" >/dev/null
fi
echo "Запускаю $name: $cmd"
# shellcheck disable=SC2086
docker run -d --name "$name" --restart unless-stopped "${VOLUMES[@]}" "$IMAGE" $cmd >/dev/null

echo ""
echo "Готово."
echo "Статус:     docker ps --filter name=priem-"
echo "Логи бота:  docker logs -f priem-bot"
echo "Остановить: ./scripts/docker_stop.sh"
