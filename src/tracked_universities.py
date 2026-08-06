from __future__ import annotations

from .models import ProgramConfig

TRACKED_UNIVERSITIES: tuple[str, ...] = (
    "Финансовый университет",
    "МЭИ",
    "МИРЭА",
    "СТАНКИН",
    # TLS mospolytech.ru (не досылает промежуточный сертификат) починен своим
    # бандлом в polytech_pool._ca_bundle(): certifi + промежуточный сертификат
    # из config/certs/globalsign-gcc-r3-dv-tls-ca-2020.pem.
    "Московский политех",
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
