from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "telegram.json"


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str
    allowed_chat_ids: list[int]


def load_telegram_config() -> TelegramConfig | None:
    if not CONFIG_PATH.exists():
        return None
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    token = str(data.get("bot_token", "")).strip()
    if not token or token.startswith("123456789:"):
        return None
    chat_ids = [int(item) for item in data.get("allowed_chat_ids", [])]
    return TelegramConfig(bot_token=token, allowed_chat_ids=chat_ids)
