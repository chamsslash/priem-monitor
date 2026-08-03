from __future__ import annotations

from .models import ProgramConfig

TRACKED_UNIVERSITIES: tuple[str, ...] = (
    "Финансовый университет",
    "МЭИ",
    "МИРЭА",
    "СТАНКИН",
    # "Московский политех" временно отключён: сервер mospolytech.ru не
    # досылает промежуточный TLS-сертификат (подтверждено live через
    # openssl s_client -showcerts — шлют только листовой сертификат), из-за
    # чего все профили стабильно валятся с SSLCertVerificationError каждый
    # цикл обновления. Проблема на их стороне. Вернуть строку обратно, когда
    # починят цепочку сертификатов.
)

_TRACKED_SET = frozenset(TRACKED_UNIVERSITIES)


def is_tracked_university(name: str) -> bool:
    return name in _TRACKED_SET


def filter_programs(programs: list[ProgramConfig]) -> list[ProgramConfig]:
    return [program for program in programs if is_tracked_university(program.university)]


def filter_rows(rows: list[dict]) -> list[dict]:
    return [row for row in rows if is_tracked_university(str(row.get("university", "")))]


def filter_results(results: dict) -> dict:
    return {**results, "rows": filter_rows(results.get("rows", []))}
