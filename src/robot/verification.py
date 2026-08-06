"""Авто-сверка результатов робота с живыми данными сайта.

Две независимые оси (см. брейншторм 2026-08-03):

1. Места (ВХОД робота) — провенанс числа мест: снято ли оно живьём с сайта
   (СТАНКИН kcp.php / МЭИ таблица КЦП / МИРЭА plan из API) или взято из
   аварийного резерва (протухший хардкод). Это ловит ту боль, что кусала дважды:
   тихой подмены мест устаревшими числами больше не будет — сверка подсветит
   «из резерва». Подробный разбор чисел — в `seat_oracle.py`.

2. Прогноз (ВЫХОД робота) — сверка предсказанного роботом зачисления Димы с
   фактическими проходными баллами среди СОГЛАСНЫХ на сайте. Робот моделирует
   Диму так, будто он прямо сейчас подал согласие и стоит в консент-пуле;
   оракул — реальный проходной балл среди согласных по каждому направлению
   (мин. балл среди тех, кого сайт туда зачислил). Идём по приоритетам робота и
   берём первое направление, где балл Димы этот порог перебивает — и сравниваем
   с прогнозом. Сверка честна ровно пока вердикт сайта о ЛЮДЯХ не участвует во
   входе каскада: места берутся из официального КЦП, а кто куда попадёт, робот
   выводит сам.
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
# СТАНКИН — колонка «Высший проходной приоритет» в конкурсном списке;
# МИРЭА — minScore бюджетного конкурса из competitions_api;
# МЭИ — колонка «Высший проходной» в списках /inform/list<N>bacc.html;
# ФА — колонка «Высший проходной приоритет» в listabit.php.
# Общее условие: вердикт сайта о людях робот НЕ читает на входе — он берёт
# только места (официальный КЦП) и строки списков, а кто куда попадёт, выводит
# своим каскадом. Пока МЭИ брал места из «вакантных мест» волны и вычёркивал
# помеченных «Зачисляется в другой КГ», сверка была бы круговой: и число мест,
# и состав конкурентов приходили из уже готового ответа сайта.
# ФА: места берутся из приказа о КЦП (PDF на fa.ru) — 40 из 45 программ. Пять
# оставшихся сидят на заглушке не по недосмотру: их нет в московской очной
# секции приказа, они существуют только в филиалах, и после фильтра по кампусу
# у них ноль абитуриентов — на каскад заглушка не влияет.
# Московский политех — колонка «Высший проходной приоритет» в ответе
# fio_list_curl.php, места — «Остаток на общий конкурс» из шапки того же
# ответа; вердикт сайта о людях на вход каскада не идёт.
PLACEMENT_VERIFIED_UNIVERSITIES = {
    "СТАНКИН",
    "МИРЭА",
    "МЭИ",
    "Финансовый университет",
    "Московский политех",
}


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
    if robot_key == site_key:
        status = "match"
    else:
        # Граница, а не расхождение: проходной балл — это минимальный балл среди
        # прошедших, и при РАВНОМ балле проходит не каждый (дальше решают ИД,
        # преимущественное право, баллы по предметам — правил тай-брейка у нас
        # нет). Оракул с его «балл >= порога» на этой границе всегда оптимистичен,
        # а каскад — как повезёт с порядком. Спорить тут не о чем: помечаем
        # отдельно, чтобы не считать это ошибкой модели и не прятать факт.
        cutoff_by_key = {program.key: program.passing_cutoff for program in programs}
        at_boundary = site_key is not None and cutoff_by_key.get(site_key) == result.dima_score
        status = "boundary" if at_boundary else "mismatch"
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
    if placement is not None and placement.status in ("mismatch", "boundary"):
        logger.warning(
            "[сверка %s] прогноз (%s): робот → %s, сайт-порог → %s",
            report.university,
            placement.status,
            placement.robot_title or "не проходит",
            placement.site_title or "не проходит",
        )
    if not fallback and (placement is None or placement.status not in ("mismatch", "boundary")):
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
