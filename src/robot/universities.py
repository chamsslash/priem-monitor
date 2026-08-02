from __future__ import annotations

from collections.abc import Callable, Iterable

from ..tracked_universities import TRACKED_UNIVERSITIES
from .fa_pool import fetch_fa_full_pool
from .mirea_pool import fetch_mirea_full_pool
from .models import RobotPerson, RobotProgram
from .mpei_pool import fetch_mpei_full_pool
from .stankin_pool import fetch_stankin_full_pool

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
    "mpei": fetch_mpei_full_pool,
    "stankin": fetch_stankin_full_pool,
}


def fetch_university_pool(parser_name: str, *, use_cache: bool = True) -> tuple[list[RobotPerson], list[RobotProgram], str, bool]:
    fetcher = _POOL_FETCHERS.get(parser_name)
    if fetcher is None:
        raise ValueError(f"Сборщик единого списка для parser={parser_name} пока не реализован")
    return fetcher(use_cache=use_cache)


def match_university_prefix(
    tokens: list[str], names: Iterable[str] | None = None
) -> tuple[str | None, int]:
    """Ищет одно из названий вузов в начале tokens.

    Название вуза может состоять из нескольких слов (например, «Финансовый
    университет»), поэтому сравнение по одному токену не годится: пробуем
    более длинные (многословные) имена раньше однословных.
    Возвращает (каноническое имя или None, число потреблённых токенов).
    """
    candidates = names if names is not None else SUPPORTED_UNIVERSITIES
    for name in sorted(candidates, key=lambda n: -len(n.split())):
        words = name.split()
        if tokens[: len(words)] == words:
            return name, len(words)
    return None, 0


def parse_university_arg(parts: list[str], *, default: str | None = None) -> str | None:
    if len(parts) < 2:
        return default
    matched, _ = match_university_prefix(parts[1:])
    return matched if matched is not None else " ".join(parts[1:])


def robot_ready_universities() -> list[str]:
    return sorted(name for name, parser in SUPPORTED_UNIVERSITIES.items() if parser in _POOL_FETCHERS)


def parse_robot_command(parts: list[str]) -> tuple[str | None, str | list[str]]:
    """Возвращает (действие, вуз или список вузов). action: None — симуляция, 'refresh' — обновить кэш."""
    if len(parts) < 2:
        return None, "МИРЭА"

    tokens = [part for part in parts[1:] if part.lower() not in REFRESH_KEYWORDS]
    refresh = len(tokens) != len(parts) - 1

    if refresh:
        if not tokens:
            return "refresh", robot_ready_universities()
        universities: list[str] = []
        i = 0
        while i < len(tokens):
            matched, consumed = match_university_prefix(tokens[i:])
            if matched is not None:
                universities.append(matched)
                i += consumed
            else:
                universities.append(tokens[i])
                i += 1
        return "refresh", universities

    university = parse_university_arg(parts, default="МИРЭА") or "МИРЭА"
    return None, university
