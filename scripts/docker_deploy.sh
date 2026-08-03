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

# SOCKS5-прокси для МИРЭА (гео-блок с зарубежного IP). Адрес — из env MIREA_PROXY,
# напр. socks5h://127.0.0.1:1080. Прокси указывает на SSH-туннель на localhost
# ХОСТА, поэтому контейнеру отдаём сетевой стек хоста (--network host) — иначе его
# 127.0.0.1 это localhost контейнера, а не хоста, и туннель недоступен.
NET_ARGS=()
if [[ -n "${MIREA_PROXY:-}" ]]; then
  NET_ARGS+=(--network host)
  echo "MIREA_PROXY задан ($MIREA_PROXY) → контейнер с --network host для доступа к туннелю."
fi

name=priem-bot
cmd="python3 scripts/telegram_bot.py"
if [[ -n "$(docker ps -aq -f "name=^${name}\$")" ]]; then
  echo "Пересоздаю контейнер $name..."
  docker rm -f "$name" >/dev/null
fi
echo "Запускаю $name: $cmd"
# shellcheck disable=SC2086
docker run -d --name "$name" --restart unless-stopped "${NET_ARGS[@]}" \
  -e MIREA_PROXY="${MIREA_PROXY:-}" "${VOLUMES[@]}" "$IMAGE" $cmd >/dev/null

echo ""
echo "Готово."
echo "Статус:     docker ps --filter name=priem-"
echo "Логи бота:  docker logs -f priem-bot"
echo "Остановить: ./scripts/docker_stop.sh"
