"""Авто-сверка результатов робота с живыми данными сайта.

Две независимые оси (см. брейншторм 2026-08-03):

1. Места (ВХОД робота) — провенанс числа мест: снято ли оно живьём с сайта
   (СТАНКИН kcp.php / МЭИ вакантные места) или взято из аварийного резерва
   (протухший хардкод). Это ловит ту боль, что кусала дважды: тихой подмены
   мест устаревшими числами больше не будет — сверка подсветит «из резерва».

2. Прогноз (ВЫХОД робота) — сверка предсказанного роботом зачисления Димы с
   вердиктом самого сайта. Оракул: СТАНКИН отдаёт колонку «Высший проходной
   приоритет» — независимый ответ, робот его на входе НЕ использует, поэтому
   сравнение честное. МЭИ сюда НЕ входит: там робот сам потребляет маркер
   «другой КГ», и сверка была бы круговой (подтверждала бы собственный вход).
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


def _dima_site_direction(raw_dima: RobotPerson | None) -> tuple[str | None, bool]:
    """Вердикт сайта по Диме через колонку «Высший проходной приоритет».

    Возвращает (program_key, куда сайт селит Диму | None; есть ли оракул).
    «Есть оракул» = хоть у одной выборки Димы site_passes_here заполнен (не None).
    Если оракул есть, но ни одна не True — сайт считает, что Дима не проходит
    ни на одно из отслеживаемых направлений (site_key = None, но оракул есть).
    """
    if raw_dima is None:
        return None, False
    have_oracle = any(choice.site_passes_here is not None for choice in raw_dima.choices)
    if not have_oracle:
        return None, False
    passing = [choice for choice in raw_dima.choices if choice.site_passes_here]
    if not passing:
        return None, True
    # Ровно одно направление должно быть «высшим проходным»; если сайт отметил
    # несколько — берём по наивысшему приоритету (наименьший priority).
    best = min(passing, key=lambda choice: choice.priority)
    return best.program_key, True


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


def _robot_uses_real_priorities(raw_dima: RobotPerson | None, sim_dima: RobotPerson | None) -> bool:
    """True, если робот гонялся ровно на реально поданных Димой приоритетах.

    Сайт считает «Основной высший приоритет» по фактически поданному списку и
    порядку. Если робот запущен на других (гипотетических) приоритетах, вердикт
    сайта несопоставим с прогнозом — строгую сверку в этом случае не проводим.
    """
    real = _priority_keys(raw_dima)
    return bool(real) and _priority_keys(sim_dima) == real


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


def _build_placement_check(
    university: str,
    result: RobotSimulationResult,
    programs: list[RobotProgram],
    raw_dima: RobotPerson | None,
    sim_dima: RobotPerson | None,
) -> PlacementCheck | None:
    if university not in PLACEMENT_VERIFIED_UNIVERSITIES:
        return None
    robot_key = result.dima_placed_program_key
    robot_title = result.dima_placed_title or _title_for_key(programs, robot_key)

    # Гейт по согласию: сайт отмечает «Высший проходной приоритет» только у
    # подавших согласие, а робот моделирует зачисление именно среди подавших.
    # Если согласия нет — оракул по этому человеку пуст, сверять нечего.
    if raw_dima is None or not raw_dima.consent:
        return PlacementCheck(
            status="no_consent",
            robot_key=robot_key,
            robot_title=robot_title,
            site_key=None,
            site_title=None,
        )

    # Строгую сверку проводим только на реально поданных приоритетах (см. гейт).
    if not _robot_uses_real_priorities(raw_dima, sim_dima):
        return PlacementCheck(
            status="hypothetical",
            robot_key=robot_key,
            robot_title=robot_title,
            site_key=None,
            site_title=None,
        )

    site_key, have_oracle = _dima_site_direction(raw_dima)
    if not have_oracle:
        status = "unavailable"
    elif robot_key == site_key:
        status = "match"
    else:
        status = "mismatch"
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
            "[сверка %s] расхождение прогноза: робот → %s, сайт → %s",
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
    raw_dima: RobotPerson | None,
    sim_dima: RobotPerson | None,
) -> VerificationReport:
    report = VerificationReport(
        university=university,
        seats=_build_seat_checks(result, programs),
        placement=_build_placement_check(university, result, programs, raw_dima, sim_dima),
    )
    _log_report(report)
    return report
