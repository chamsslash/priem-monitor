from __future__ import annotations

import logging
import time
from pathlib import Path

from .telegram_api import TelegramAPIError, send_message
from .telegram_config import TelegramConfig, load_telegram_config
from .telegram_format import (
    format_push_message,
    format_status_message,
    format_university_messages,
    university_names,
    university_rows,
)

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
MESSAGE_DELAY_SEC = 0.35


def _chat_ids(config: TelegramConfig, chat_id: int | None = None) -> list[int]:
    if chat_id is not None:
        return [chat_id]
    return list(config.allowed_chat_ids)


def is_allowed(config: TelegramConfig, chat_id: int) -> bool:
    return chat_id in config.allowed_chat_ids


def build_university_keyboard(results: dict) -> dict:
    buttons: list[list[dict[str, str]]] = []
    row: list[dict[str, str]] = []

    for index, university in enumerate(university_names(results)):
        row.append({"text": university, "callback_data": f"uni:{index}"})
        if len(row) == 2:
            buttons.append(row)
            row = []

    if row:
        buttons.append(row)

    return {"inline_keyboard": buttons}


def send_to_chats(
    config: TelegramConfig,
    text: str,
    chat_id: int | None = None,
    reply_markup: dict | None = None,
    *,
    parse_mode: str | None = None,
) -> None:
    for target in _chat_ids(config, chat_id):
        try:
            send_message(config.bot_token, target, text, reply_markup=reply_markup, parse_mode=parse_mode)
        except TelegramAPIError as exc:
            logger.error("Не удалось отправить сообщение в %s: %s", target, exc)


def push_update(old_results: dict, new_results: dict) -> bool:
    config = load_telegram_config()
    if not config or not config.allowed_chat_ids:
        return False

    send_to_chats(config, format_push_message(old_results, new_results))
    return True


def send_status(results: dict, chat_id: int) -> None:
    config = load_telegram_config()
    if not config:
        raise RuntimeError("Не настроен config/telegram.json")
    send_to_chats(
        config,
        format_status_message(results),
        chat_id=chat_id,
        reply_markup=build_university_keyboard(results),
    )


def build_back_to_menu_keyboard() -> dict:
    return {"inline_keyboard": [[{"text": "← Вернуться к меню вузов", "callback_data": "menu:back"}]]}


def send_university_report(results: dict, chat_id: int, university_index: int) -> str:
    names = university_names(results)
    if university_index < 0 or university_index >= len(names):
        raise ValueError("Неизвестный вуз")

    university = names[university_index]
    rows = university_rows(results, university)
    messages = format_university_messages(university, rows, results.get("generated_at"))

    config = load_telegram_config()
    if not config:
        raise RuntimeError("Не настроен config/telegram.json")

    for index, text in enumerate(messages):
        is_last = index == len(messages) - 1
        send_to_chats(
            config,
            text,
            chat_id=chat_id,
            reply_markup=build_back_to_menu_keyboard() if is_last else None,
            parse_mode="HTML",
        )
        if not is_last:
            time.sleep(MESSAGE_DELAY_SEC)

    return university
