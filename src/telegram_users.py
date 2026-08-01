from __future__ import annotations

import json
import re
from pathlib import Path

from .robot.config import RobotSettings

ROOT = Path(__file__).resolve().parents[1]
USERS_PATH = ROOT / "data" / "telegram_users.json"
USER_ROBOT_CONFIG_DIR = ROOT / "data" / "telegram_users"

_CODE_RE = re.compile(r"^\d{4,15}$")


def looks_like_code(text: str) -> bool:
    return bool(_CODE_RE.match(text.strip()))


def _load_all() -> dict[str, str]:
    if not USERS_PATH.exists():
        return {}
    try:
        return json.loads(USERS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        return {}


def _save_all(data: dict[str, str]) -> None:
    USERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    USERS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def get_user_code(chat_id: int) -> str | None:
    return _load_all().get(str(chat_id))


def is_registered(chat_id: int) -> bool:
    return get_user_code(chat_id) is not None


def robot_config_path(chat_id: int) -> Path:
    return USER_ROBOT_CONFIG_DIR / f"{chat_id}.json"


def _ensure_blank_robot_config(chat_id: int) -> None:
    """Гарантирует, что личный robot.json-подобный файл существует и пуст.

    Критично: src/robot/priorities.py::load_raw_config() при отсутствии
    файла по указанному path падает обратно на config/robot.example.json —
    а он НЕ пустой шаблон, там реальные Димины dima_priorities. Без явного
    создания пустого файла новый пользователь получил бы Димины приоритеты
    по умолчанию вместо "нет приоритетов = берём реальный поданный порядок".
    """
    path = robot_config_path(chat_id)
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"universities": {}}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def set_user_code(chat_id: int, code: str) -> None:
    data = _load_all()
    data[str(chat_id)] = code
    _save_all(data)
    _ensure_blank_robot_config(chat_id)


def build_robot_settings(code: str, university: str) -> RobotSettings:
    """Личность для симулятора строится на лету из кода пользователя —
    без обращения к config/robot.json. dima_score/dima_consent здесь не
    влияют на результат, когда код реально находится в пуле: в этом
    случае _resolve_dima_person() в simulator.py берёт настоящий балл
    прямо из пула (found.score), эти поля — только безопасный дефолт для
    редкой ветки "код не найден" (синтетический участник)."""
    return RobotSettings(
        dima_code=code,
        dima_score=0,
        dima_consent=True,
        require_consent=True,
        universities={
            university: {
                "enabled": True,
                "dima_list_code": code,
            }
        },
    )
