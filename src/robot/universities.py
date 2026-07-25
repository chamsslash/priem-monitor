from __future__ import annotations

from collections.abc import Callable

from ..tracked_universities import TRACKED_UNIVERSITIES
from .fa_pool import fetch_fa_full_pool
from .mirea_pool import fetch_mirea_full_pool
from .models import RobotPerson, RobotProgram

PoolFetcher = Callable[..., tuple[list[RobotPerson], list[RobotProgram], str, bool]]

SUPPORTED_UNIVERSITIES: dict[str, str] = {
    "Финансовый университет": "fa",
    "МЭИ": "mpei",
    "МИРЭА": "mirea",
    "СТАНКИН": "stankin",
    "Московский политех": "mospolytech",
}

if set(SUPPORTED_UNIVERSITIES) != set(TRACKED_UNIVERSITIES):
    raise RuntimeError("SUPPORTED_UNIVERSITIES и TRACKED_UNIVERSITIES должны совпадать")

REFRESH_KEYWORDS = {"обновить", "refresh", "кэш", "cache"}

_POOL_FETCHERS: dict[str, PoolFetcher] = {
    "fa": fetch_fa_full_pool,
    "mirea": fetch_mirea_full_pool,
}


def fetch_university_pool(parser_name: str, *, use_cache: bool = True) -> tuple[list[RobotPerson], list[RobotProgram], str, bool]:
    fetcher = _POOL_FETCHERS.get(parser_name)
    if fetcher is None:
        raise ValueError(f"Сборщик единого списка для parser={parser_name} пока не реализован")
    return fetcher(use_cache=use_cache)


def parse_university_arg(parts: list[str], *, default: str | None = None) -> str | None:
    if len(parts) < 2:
        return default
    candidate = parts[1]
    if candidate in SUPPORTED_UNIVERSITIES:
        return candidate
    return default


def robot_ready_universities() -> list[str]:
    return sorted(name for name, parser in SUPPORTED_UNIVERSITIES.items() if parser in _POOL_FETCHERS)


def parse_robot_command(parts: list[str]) -> tuple[str | None, str | list[str]]:
    """Возвращает (действие, вуз или список вузов). action: None — симуляция, 'refresh' — обновить кэш."""
    if len(parts) < 2:
        return None, "МИРЭА"

    refresh = any(part.lower() in REFRESH_KEYWORDS for part in parts[1:])
    universities = [part for part in parts[1:] if part in SUPPORTED_UNIVERSITIES]

    if refresh:
        if not universities:
            return "refresh", robot_ready_universities()
        return "refresh", universities

    university = parse_university_arg(parts, default="МИРЭА") or "МИРЭА"
    return None, university
