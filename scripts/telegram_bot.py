#!/usr/bin/env python3
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.run_update import UpdateStatus, run_update
from src.service import load_results
from src.telegram_api import TelegramAPIError, answer_callback_query, get_updates, load_offset, save_offset, send_message
from src.telegram_config import load_telegram_config
from src.telegram_format import format_push_message
from src.telegram_notify import is_allowed, send_status, send_to_chats, send_university_report

LOG_PATH = ROOT / "logs" / "telegram_bot.log"
OFFSET_PATH = ROOT / "logs" / "telegram_offset.json"

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("telegram_bot")


def _command(text: str) -> str:
    return text.strip().split()[0].split("@")[0].lower()


def _welcome(chat_id: int) -> str:
    return (
        "Бот мониторинга поступления.\n\n"
        "Команды:\n"
        "/статус — общая сводка и выбор вуза\n"
        "/обновить — загрузить свежие списки\n"
        "/help — справка\n\n"
        f"Ваш chat_id: {chat_id}\n"
        "Передайте его администратору, чтобы получить доступ."
    )


def _help_text() -> str:
    return (
        "Доступные команды:\n"
        "/статус — общая сводка и меню выбора вуза\n"
        "/обновить — запустить парсинг (работает, пока включён Mac)\n"
        "/help — эта справка"
    )


def _handle_callback(config, callback_query: dict) -> None:
    callback_id = callback_query["id"]
    data = callback_query.get("data") or ""
    message = callback_query.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return

    chat_id = int(chat_id)

    if not is_allowed(config, chat_id):
        answer_callback_query(config.bot_token, callback_id, text="Нет доступа")
        return

    if data == "menu:back":
        results = load_results()
        if not results.get("rows"):
            answer_callback_query(config.bot_token, callback_id, text="Нет данных")
            return
        send_status(results, chat_id)
        answer_callback_query(config.bot_token, callback_id, text="Меню вузов")
        return

    if not data.startswith("uni:"):
        answer_callback_query(config.bot_token, callback_id)
        return

    try:
        university_index = int(data.split(":", 1)[1])
    except ValueError:
        answer_callback_query(config.bot_token, callback_id, text="Некорректный выбор")
        return

    results = load_results()
    if not results.get("rows"):
        answer_callback_query(config.bot_token, callback_id, text="Нет данных")
        return

    try:
        university = send_university_report(results, chat_id, university_index)
        answer_callback_query(config.bot_token, callback_id, text=university)
    except (ValueError, RuntimeError) as exc:
        answer_callback_query(config.bot_token, callback_id, text=str(exc))
    except TelegramAPIError as exc:
        logger.error("Callback failed for chat %s: %s", chat_id, exc)
        answer_callback_query(config.bot_token, callback_id, text="Ошибка отправки")


def _handle_message(config, chat_id: int, text: str, message_id: int) -> None:
    command = _command(text)

    if command in {"/start", "/help"}:
        send_message(config.bot_token, chat_id, _welcome(chat_id) if command == "/start" else _help_text(), reply_to=message_id)
        return

    if not is_allowed(config, chat_id):
        send_message(
            config.bot_token,
            chat_id,
            f"Нет доступа. Ваш chat_id: {chat_id}\nПопросите администратора добавить его в config/telegram.json",
            reply_to=message_id,
        )
        return

    if command == "/статус":
        results = load_results()
        if not results.get("rows"):
            send_message(config.bot_token, chat_id, "Данных пока нет. Запустите /обновить.", reply_to=message_id)
            return
        send_status(results, chat_id)
        return

    if command in {"/обновить", "/update"}:
        send_message(config.bot_token, chat_id, "Запускаю обновление… Это может занять 1–2 минуты.", reply_to=message_id)
        old_results = load_results()
        result = run_update()
        if result.status == UpdateStatus.ALREADY_RUNNING:
            send_message(config.bot_token, chat_id, result.message, reply_to=message_id)
            return
        if result.status != UpdateStatus.SUCCESS:
            send_message(config.bot_token, chat_id, f"Ошибка обновления:\n{result.message}", reply_to=message_id)
            return

        new_results = load_results()
        send_to_chats(config, format_push_message(old_results, new_results), chat_id=chat_id)
        return

    send_message(config.bot_token, chat_id, "Неизвестная команда. Напишите /help", reply_to=message_id)


def main() -> int:
    config = load_telegram_config()
    if not config:
        print("Создайте config/telegram.json на основе config/telegram.example.json", file=sys.stderr)
        return 1

    offset = load_offset(OFFSET_PATH)
    logger.info("Бот запущен. offset=%s", offset)

    while True:
        try:
            updates = get_updates(config.bot_token, offset=offset, timeout=30)
        except TelegramAPIError as exc:
            logger.error("getUpdates failed: %s", exc)
            time.sleep(5)
            continue
        except Exception as exc:
            logger.exception("Unexpected polling error: %s", exc)
            time.sleep(5)
            continue

        for update in updates:
            offset = int(update["update_id"]) + 1
            save_offset(OFFSET_PATH, offset)

            callback_query = update.get("callback_query")
            if callback_query:
                try:
                    _handle_callback(config, callback_query)
                except Exception as exc:
                    logger.exception("Callback crashed: %s", exc)
                continue

            message = update.get("message") or update.get("edited_message")
            if not message:
                continue

            text = message.get("text") or ""
            if not text.startswith("/"):
                continue

            chat = message.get("chat") or {}
            chat_id = chat.get("id")
            if chat_id is None:
                continue

            try:
                _handle_message(config, int(chat_id), text, int(message["message_id"]))
            except TelegramAPIError as exc:
                logger.error("Command failed for chat %s: %s", chat_id, exc)
                try:
                    send_message(config.bot_token, int(chat_id), f"Ошибка: {exc}")
                except TelegramAPIError:
                    pass
            except Exception as exc:
                logger.exception("Command crashed for chat %s: %s", chat_id, exc)


if __name__ == "__main__":
    raise SystemExit(main())
