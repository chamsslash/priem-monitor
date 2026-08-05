#!/usr/bin/env python3
"""Сверка робота с официальными сайтами: места и прогноз.

Что проверяет по каждому вузу:

1. **Места** (`src/robot/seat_oracle.py`) — число мест, которое взял робот, и
   захардкоженный локальный резерв против официального числа с сайта. Ловит ту
   боль, из-за которой места дважды протухали: резерв разъезжается с сайтом
   молча, и робот уверенно считает по устаревшему числу.
2. **Платные места** — в пуле не должно быть ни одного платного конкурса:
   конкурент, «зачисленный» на платное место, исчезает из борьбы за бюджет, и
   робот видит свободный бюджет там, где его нет.
3. **Прогноз** — куда робот селит пользователя против вердикта самого сайта
   (проходной балл среди согласных). Только там, где у сайта есть независимый
   оракул: СТАНКИН и МИРЭА.

Использование:

    python3 scripts/check_seats.py               # из кэша пулов (быстро)
    python3 scripts/check_seats.py --fresh       # пересобрать пулы из сети
    python3 scripts/check_seats.py --university МЭИ

Код возврата 1 — есть проблемы (протухший резерв, число вне границ, расхождение
прогноза). 0 — всё сходится.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.robot.models import RobotProgram
from src.robot.seat_oracle import SeatAudit, audit_university, check_no_paid_seats
from src.robot.simulator import run_robot_simulation
from src.robot.universities import (
    SUPPORTED_UNIVERSITIES,
    fetch_university_pool,
    read_cached_pool,
)

MARKS = {
    "ok": "OK  ",
    "stale_local": "СТАР",
    "mismatch": "!!! ",
    "no_oracle": "-   ",
    "unavailable": "?   ",
}


def _load_programs(university: str, *, fresh: bool) -> tuple[list[RobotProgram], str]:
    parser_name = SUPPORTED_UNIVERSITIES[university]
    if fresh:
        _, programs, fetched_at, _ = fetch_university_pool(parser_name, use_cache=False)
        return programs, fetched_at
    cached = read_cached_pool(parser_name)
    if cached is None:
        raise RuntimeError("кэша пула нет — запусти с --fresh")
    _, programs, fetched_at, _ = cached
    return programs, fetched_at


def _print_seats(audits: list[SeatAudit]) -> None:
    print(f"  {'напр.':<44} {'робот':>6} {'источник':>9} {'офиц.':>6} {'резерв':>7}  статус")
    for audit in audits:
        print(
            f"  {audit.title[:43]:<44} "
            f"{str(audit.robot_places):>6} {str(audit.robot_source):>9} "
            f"{str(audit.official_places):>6} {str(audit.local_places):>7}  "
            f"{MARKS.get(audit.status, audit.status)} {audit.note}"
        )


def _print_paid(university: str, programs: list[RobotProgram]) -> bool:
    total_places = sum(p.budget_places or 0 for p in programs)
    try:
        ok, note = check_no_paid_seats(university, programs)
    except Exception as exc:  # noqa: BLE001
        print(f"  платное: проверить не удалось ({exc})")
        return True
    prefix = "платное" if ok else "ПЛАТНОЕ В ПУЛЕ"
    print(f"  {prefix}: {note}; итого {len(programs)} конкурсов, {total_places} мест")
    return ok


def _print_placement(university: str) -> bool:
    result = run_robot_simulation(university, stale_ok=True)
    if result.error:
        print(f"  прогноз: не проверен ({result.error})")
        return True
    report = result.verification
    check = report.placement if report else None
    if check is None:
        print("  прогноз: у вуза нет независимого оракула сайта — сверка не проводится")
        return True
    if check.status == "unavailable":
        print("  прогноз: сайт не отдал проходные баллы — сверять не с чем")
        return True
    robot = check.robot_title or "никуда не проходит"
    site = check.site_title or "никуда не проходит"
    if check.status == "match":
        print(f"  прогноз: совпал с сайтом — {robot}")
        return True
    print(f"  прогноз: РАСХОЖДЕНИЕ — робот: {robot} | сайт-порог: {site}")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Сверка мест и прогноза робота с сайтами вузов")
    parser.add_argument("--fresh", action="store_true", help="пересобрать пулы из сети")
    parser.add_argument("--university", action="append", help="только этот вуз (можно несколько раз)")
    args = parser.parse_args()

    universities = args.university or list(SUPPORTED_UNIVERSITIES)
    unknown = [item for item in universities if item not in SUPPORTED_UNIVERSITIES]
    if unknown:
        print(f"Неизвестный вуз: {unknown}. Доступны: {list(SUPPORTED_UNIVERSITIES)}")
        return 2

    problems = 0
    for university in universities:
        print(f"\n=== {university} ===")
        try:
            programs, fetched_at = _load_programs(university, fresh=args.fresh)
        except Exception as exc:  # noqa: BLE001
            print(f"  пул недоступен: {exc}")
            problems += 1
            continue
        print(f"  пул от {fetched_at}")

        try:
            audits = audit_university(university, programs)
        except Exception as exc:  # noqa: BLE001
            print(f"  оракул мест недоступен: {exc}")
            problems += 1
        else:
            _print_seats(audits)
            problems += sum(1 for audit in audits if audit.is_problem)

        if not _print_paid(university, programs):
            problems += 1
        if not _print_placement(university):
            problems += 1

    print(f"\nИтого проблем: {problems}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
