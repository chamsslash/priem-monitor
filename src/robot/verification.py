"""Авто-сверка результатов робота с живыми данными сайта.

Две независимые оси (см. брейншторм 2026-08-03):

1. Места (ВХОД робота) — провенанс числа мест: снято ли оно живьём с сайта
   (СТАНКИН kcp.php / МЭИ вакантные места) или взято из аварийного резерва
   (протухший хардкод). Это ловит ту боль, что кусала дважды: тихой подмены
   мест устаревшими числами больше не будет — сверка подсветит «из резерва».

2. Прогноз (ВЫХОД робота, пока только СТАНКИН) — сверка предсказанного роботом
   зачисления Димы с фактическими проходными баллами среди СОГЛАСНЫХ на сайте.
   Робот моделирует Диму так, будто он прямо сейчас подал согласие и стоит в
   консент-пуле; оракул — реальный проходной балл среди согласных по каждому
   направлению (мин. балл среди тех, кого сайт туда зачислил). Идём по приоритетам
   робота и берём первое направление, где балл Димы этот порог перебивает — и
   сравниваем с прогнозом. Не круговая: порог берётся из фактических зачислений
   сайта (их движок), а робот считает своим каскадом на живых местах. МЭИ сюда
   НЕ входит: там робот сам потребляет маркер «другой КГ», сверка была бы круговой.
"""

from __future__ import annotations

import logging

from .models import (
    PlacementCheck,
    RobotPerson,
    RobotProgram,
    RobotSimulationResult,
    SeatCheck,
    VerificationReport,
)

logger = logging.getLogger(__name__)

# Вузы, где сверка прогноза честная (независимый оракул сайта).
PLACEMENT_VERIFIED_UNIVERSITIES = {"СТАНКИН"}


def _title_for_key(programs: list[RobotProgram], key: str | None) -> str | None:
    if key is None:
        return None
    return next((program.title for program in programs if program.key == key), None)


def _priority_keys(person: RobotPerson | None) -> list[str]:
    """Ключи направлений в порядке приоритета (без дублей)."""
    if person is None:
        return []
    seen: set[str] = set()
    keys: list[str] = []
    for choice in sorted(person.choices, key=lambda item: item.priority):
        if choice.program_key in seen:
            continue
        seen.add(choice.program_key)
        keys.append(choice.program_key)
    return keys


def _build_seat_checks(
    result: RobotSimulationResult, programs: list[RobotProgram]
) -> list[SeatCheck]:
    by_key = {program.key: program for program in programs}
    checks: list[SeatCheck] = []
    for state in result.tracked_programs:
        program = by_key.get(state.program_key)
        checks.append(
            SeatCheck(
                program_key=state.program_key,
                title=state.title,
                tracked_id=state.tracked_id,
                budget_places=state.budget_places,
                seat_source=program.seat_source if program else None,
            )
        )
    return checks


def _site_placement(
    programs: list[RobotProgram], sim_dima: RobotPerson | None, dima_score: int
) -> str | None:
    """Куда сайт-порог селит Диму: первое по приоритету направление, где его балл
    перебивает реальный проходной среди согласных."""
    cutoff_by_key = {program.key: program.passing_cutoff for program in programs}
    for key in _priority_keys(sim_dima):
        cutoff = cutoff_by_key.get(key)
        if cutoff is None:
            continue
        if dima_score >= cutoff:
            return key
    return None


def _build_placement_check(
    university: str,
    result: RobotSimulationResult,
    programs: list[RobotProgram],
    sim_dima: RobotPerson | None,
) -> PlacementCheck | None:
    if university not in PLACEMENT_VERIFIED_UNIVERSITIES:
        return None
    robot_key = result.dima_placed_program_key
    robot_title = result.dima_placed_title or _title_for_key(programs, robot_key)

    # Нет ни одного проходного балла (колонки не было) — сверять не с чем.
    if all(program.passing_cutoff is None for program in programs):
        return PlacementCheck(
            status="unavailable",
            robot_key=robot_key,
            robot_title=robot_title,
            site_key=None,
            site_title=None,
        )

    site_key = _site_placement(programs, sim_dima, result.dima_score)
    status = "match" if robot_key == site_key else "mismatch"
    return PlacementCheck(
        status=status,
        robot_key=robot_key,
        robot_title=robot_title,
        site_key=site_key,
        site_title=_title_for_key(programs, site_key),
    )


def _log_report(report: VerificationReport) -> None:
    fallback = report.fallback_seats
    if fallback:
        details = ", ".join(
            f"{check.title}={check.budget_places}({check.seat_source})" for check in fallback
        )
        logger.warning("[сверка %s] места из резерва: %s", report.university, details)
    placement = report.placement
    if placement is not None and placement.status == "mismatch":
        logger.warning(
            "[сверка %s] расхождение прогноза: робот → %s, сайт-порог → %s",
            report.university,
            placement.robot_title or "не проходит",
            placement.site_title or "не проходит",
        )
    if not fallback and (placement is None or placement.status != "mismatch"):
        logger.info(
            "[сверка %s] места живьём: %s; прогноз: %s",
            report.university,
            report.all_seats_live,
            placement.status if placement else "n/a",
        )


def build_verification_report(
    university: str,
    result: RobotSimulationResult,
    programs: list[RobotProgram],
    *,
    sim_dima: RobotPerson | None,
) -> VerificationReport:
    report = VerificationReport(
        university=university,
        seats=_build_seat_checks(result, programs),
        placement=_build_placement_check(university, result, programs, sim_dima),
    )
    _log_report(report)
    return report
