from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import requests

API_BASE = "https://api.telegram.org/bot{token}/{method}"


class TelegramAPIError(RuntimeError):
    pass


def _call(token: str, method: str, payload: dict[str, Any] | None = None, files: dict | None = None) -> dict:
    url = API_BASE.format(token=token, method=method)
    response = requests.post(url, data=payload or {}, files=files, timeout=60)
    if not response.ok:
        # НЕ response.raise_for_status(): текст HTTPError содержит полный URL
        # запроса, а вместе с ним и токен бота (см. API_BASE) — такой текст
        # разлетался бы и в логи, и в сообщения пользователю (except-блоки в
        # telegram_bot.py шлют текст исключения в чат как есть).
        raise TelegramAPIError(f"{response.status_code}: {method}")
    data = response.json()
    if not data.get("ok"):
        raise TelegramAPIError(data.get("description", "Telegram API error"))
    return data["result"]


def send_message(
    token: str,
    chat_id: int,
    text: str,
    reply_to: int | None = None,
    reply_markup: dict[str, Any] | None = None,
    parse_mode: str | None = None,
) -> None:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if reply_to is not None:
        payload["reply_to_message_id"] = reply_to
    if reply_markup is not None:
        payload["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
    if parse_mode is not None:
        payload["parse_mode"] = parse_mode
    _call(token, "sendMessage", payload)


TELEGRAM_MESSAGE_LIMIT = 4096


def _split_into_chunks(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in text.split("\n"):
        extra = len(line) + (1 if current else 0)
        if current and current_len + extra > limit:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
            extra = len(line)
        current.append(line)
        current_len += extra
    if current:
        chunks.append("\n".join(current))
    return chunks


def send_long_message(
    token: str,
    chat_id: int,
    text: str,
    reply_to: int | None = None,
) -> None:
    """Как send_message, но режет текст по границам строк на части ≤4096 символов —
    лимит Telegram sendMessage, иначе API отвечает 400 Bad Request."""
    for index, chunk in enumerate(_split_into_chunks(text)):
        send_message(token, chat_id, chunk, reply_to=reply_to if index == 0 else None)


def edit_message_text(
    token: str,
    chat_id: int,
    message_id: int,
    text: str,
    reply_markup: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if reply_markup is not None:
        payload["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
    _call(token, "editMessageText", payload)


def answer_callback_query(token: str, callback_query_id: str, text: str | None = None) -> None:
    payload: dict[str, Any] = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    _call(token, "answerCallbackQuery", payload)


def send_document(token: str, chat_id: int, filename: str, content: str, caption: str | None = None) -> None:
    files = {"document": (filename, content.encode("utf-8"), "text/plain")}
    payload: dict[str, Any] = {"chat_id": chat_id}
    if caption:
        payload["caption"] = caption
    _call(token, "sendDocument", payload, files=files)


def get_updates(token: str, offset: int | None = None, timeout: int = 30) -> list[dict]:
    payload: dict[str, Any] = {"timeout": timeout}
    if offset is not None:
        payload["offset"] = offset
    return _call(token, "getUpdates", payload)


def load_offset(path: Path) -> int | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    value = data.get("offset")
    return int(value) if value is not None else None


def save_offset(path: Path, offset: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"offset": offset}, ensure_ascii=False, indent=2), encoding="utf-8")
