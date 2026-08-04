from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime, timezone

from ..tracked_universities import TRACKED_UNIVERSITIES
from .fa_pool import CACHE_TTL_SEC as FA_CACHE_TTL_SEC
from .fa_pool import fetch_fa_full_pool, read_fa_cached_pool
from .mirea_pool import CACHE_TTL_SEC as MIREA_CACHE_TTL_SEC
from .mirea_pool import fetch_mirea_full_pool, read_mirea_cached_pool
from .models import RobotPerson, RobotProgram
from .mpei_pool import CACHE_TTL_SEC as MPEI_CACHE_TTL_SEC
from .mpei_pool import fetch_mpei_full_pool, read_mpei_cached_pool
from .stankin_pool import CACHE_TTL_SEC as STANKIN_CACHE_TTL_SEC
from .stankin_pool import fetch_stankin_full_pool, read_stankin_cached_pool

CachedPool = tuple[list[RobotPerson], list[RobotProgram], str, bool]
PoolFetcher = Callable[..., CachedPool]
CacheReader = Callable[[], CachedPool | None]

SUPPORTED_UNIVERSITIES: dict[str, str] = {
    "Финансовый университет": "fa",
    "МЭИ": "mpei",
    "МИРЭА": "mirea",
    "СТАНКИН": "stankin",
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


_CACHE_READERS: dict[str, CacheReader] = {
    "fa": read_fa_cached_pool,
    "mirea": read_mirea_cached_pool,
    "mpei": read_mpei_cached_pool,
    "stankin": read_stankin_cached_pool,
}

# TTL берём из самих пулов, а не дублируем число: если пул поменяет свой срок
# жизни кэша, «протухание» здесь поедет за ним автоматически.
_CACHE_TTLS: dict[str, int] = {
    "fa": FA_CACHE_TTL_SEC,
    "mirea": MIREA_CACHE_TTL_SEC,
    "mpei": MPEI_CACHE_TTL_SEC,
    "stankin": STANKIN_CACHE_TTL_SEC,
}

if set(_CACHE_READERS) != set(_POOL_FETCHERS) or set(_CACHE_TTLS) != set(_POOL_FETCHERS):
    raise RuntimeError("_CACHE_READERS/_CACHE_TTLS должны покрывать те же парсеры, что и _POOL_FETCHERS")


def read_cached_pool(parser_name: str) -> CachedPool | None:
    """Кэш вуза любого возраста, без единого сетевого запроса.

    Точка входа для обработчиков команд. None — кэша нет (первый запуск либо
    формат кэша устарел), тогда вызывающий обязан честно сказать «данные ещё
    собираются», а не пытаться собрать их сам.
    """
    reader = _CACHE_READERS.get(parser_name)
    if reader is None:
        raise ValueError(f"Читатель кэша для parser={parser_name} не зарегистрирован")
    return reader()


def is_pool_stale(parser_name: str, fetched_at: str | None, *, max_age: int | None = None) -> bool:
    """Старше ли кэш своего TTL. Нечитаемая/пустая метка времени = считаем протухшим.

    max_age — необязательный порог в секундах вместо TTL пула. Нужен шедулеру
    прогрева: его период короче TTL, и без своего порога он не пересобирал бы
    ничего до истечения полного TTL пула.
    """
    if not fetched_at:
        return True
    ttl = max_age if max_age is not None else _CACHE_TTLS.get(parser_name)
    if ttl is None:
        return True
    try:
        fetched_dt = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    return (datetime.now(timezone.utc) - fetched_dt).total_seconds() > ttl


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
