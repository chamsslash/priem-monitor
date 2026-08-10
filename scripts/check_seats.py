#!/usr/bin/env python3
"""Сверка робота с официальными сайтами: места и прогноз.

Что проверяет по каждому вузу:

1. **Места** (`src/robot/seat_oracle.py`) — число мест, которое взял робот, и
   захардкоженный локальный резерв против официального числа с сайта. Ловит ту
   боль, из-за которой места дважды протухали: резерв разъезжается с сайтом
   молча, и робот уверенно считает по устаревшему числу. Работает для МЭИ,
   СТАНКИН, МИРЭА и ФА; у Политеха независимого оракула мест нет по замыслу —
   робот и берёт, и мог бы сверять только с одной и той же страницей.
2. **Платные места** — в пуле не должно быть ни одного платного конкурса:
   конкурент, «зачисленный» на платное место, исчезает из борьбы за бюджет, и
   робот видит свободный бюджет там, где его нет.
3. **Прогноз** — куда робот селит пользователя против вердикта самого сайта
   (проходной балл среди согласных). Работает там, где вердикт сайта о людях не
   участвует во входе каскада — сейчас это все пять вузов (см.
   `PLACEMENT_VERIFIED_UNIVERSITIES` в `verification.py`).
4. **Проходные баллы** — порог робота против порога сайта по каждому
   направлению. Систематический сдвиг вниз означает, что каскад пускает на
   места тех, кто туда не проходит.

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
from src.robot.seat_oracle import AUDITED_UNIVERSITIES, SeatAudit, audit_university, check_no_paid_seats
from src.robot.simulator import _passing_score_for_program, run_robot_simulation
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


def _pool_people(university: str) -> list:
    cached = read_cached_pool(SUPPORTED_UNIVERSITIES[university])
    return cached[0] if cached else []


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


def _print_transfers(programs: list[RobotProgram]) -> None:
    """Перенос незаполненных квот в общий конкурс: план против волны.

    Плановое число мест общего конкурса (КЦП минус квоты) и число мест, реально
    выставленных на волну, законно расходятся в обе стороны: вверх — вуз вернул
    незаполненные квотные места всем, вниз — часть мест уже занята прошлыми
    приказами. Это не ошибка и не повод менять вход каскада, а контекст: он
    показывает, насколько основной прогноз пессимистичен.
    """
    rows = [
        program
        for program in programs
        if program.vacant_places is not None or program.quota_shortfall is not None
    ]
    if not rows:
        return
    print(
        f"  {'напр. (предикт против плана и волны)':<52} "
        f"{'план':>5} {'недоб':>6} {'предикт':>8} {'волна':>6} {'Δ':>5}"
    )
    for program in sorted(rows, key=lambda item: -(item.quota_shortfall or 0)):
        predicted = program.predicted_places
        # Δ — расхождение НАШЕГО предиктa с тем, что вуз выставил на волну.
        # Это перекрёстная проверка предикта, а не ошибка: часть мест бывает
        # уже занята прошлыми приказами, и волна оказывается ниже предикта.
        if predicted is not None and program.vacant_places is not None:
            delta = f"{predicted - program.vacant_places:+d}"
        else:
            delta = "—"
        print(
            f"  {program.title[:51]:<52} {str(program.budget_places):>5} "
            f"{str(program.quota_shortfall):>6} {str(predicted):>8} "
            f"{str(program.vacant_places):>6} {delta:>5}"
        )
    shortfall = sum(program.quota_shortfall or 0 for program in rows)
    checked = [
        abs(program.predicted_places - program.vacant_places)
        for program in rows
        if program.predicted_places is not None and program.vacant_places is not None
    ]
    line = f"  итого: +{shortfall} мест возвращено из незаполненных квот"
    if checked:
        exact = sum(1 for value in checked if value == 0)
        line += (
            f"; предикт против волны: точно на {exact}/{len(checked)}, "
            f"средняя ошибка {sum(checked) / len(checked):.1f}, макс {max(checked)}"
        )
    print(line)


# Доля мест, которую сайт должен САМ отметить проходящими, чтобы его проходной
# балл имел смысл порога. Ниже неё сравнивать нечего: на «Международной экономике
# и торговле» ФА сайт отметил ОДНОГО человека на 52 места, и «проходной балл 310»
# — это просто его балл. Такие направления тянули среднюю метрику в минус на 8
# баллов и маскировали настоящие расхождения.
_ORACLE_FULL_RATIO = 0.5


def _oracle_is_full(program: RobotProgram) -> bool:
    """Можно ли считать вердикт сайта по направлению полным.

    Сравниваем с местами ВОЛНЫ, если вуз их публикует, и только иначе — с
    плановым КЦП. У МЭИ число отмеченных проходящими равно числу вакантных мест
    ровно (75=75, 170=170): сайт заполняет именно волну, а не план. Сравнение с
    планом выбросило бы из метрики «Фундаментальную информатику», где на волне
    6 мест против 13 плановых, хотя вердикт там как раз полный.

    Нет счётчика проходящих (вуз его не публикует) — считаем полным: это
    прежнее поведение, и лучше лишний раз показать расхождение, чем спрятать.
    """
    seats = program.vacant_places if program.vacant_places is not None else program.budget_places
    if program.site_passing_count is None or not seats:
        return True
    return program.site_passing_count >= _ORACLE_FULL_RATIO * seats


def _print_cutoffs(university: str, programs: list[RobotProgram]) -> None:
    """Проходной балл робота против проходного балла сайта по каждому направлению.

    Более чувствительная проверка, чем сверка одного прогноза: она мерит не
    «совпал/не совпал» на одном человеке, а насколько похоже робот и сайт
    заполняют места вообще. Систематический сдвиг вниз означает, что каскад
    пускает на места тех, кто по правде туда не проходит — так и нашлась
    подмена БВИ преимущественным правом у МИРЭА (разрыв в 58 баллов).

    Числа не обязаны совпадать в точности: робот считает только по подавшим
    согласие, а сайт — по своему кругу участников. Смотреть надо на порядок
    расхождения, а не на равенство.
    """
    result = run_robot_simulation(university, stale_ok=True)
    if result.error:
        return
    if not any(program.passing_cutoff is not None for program in programs):
        return
    states = {state.program_key: state for state in result.programs}
    people_by_code = {person.code: person for person in _pool_people(university)}

    full: list[int] = []
    thin: list[int] = []
    for program in programs:
        state = states.get(program.key)
        if state is None or not program.passing_cutoff:
            continue
        robot_cutoff = _passing_score_for_program(state, people_by_code)
        if robot_cutoff is None:
            continue
        diff = robot_cutoff - program.passing_cutoff
        (full if _oracle_is_full(program) else thin).append(diff)

    if full:
        below = sum(1 for value in full if value < 0)
        print(
            f"  проходные баллы (вердикт сайта полный): {len(full)} направлений, "
            f"средний сдвиг робота {sum(full) / len(full):+.1f}, "
            f"макс |Δ| {max(abs(value) for value in full)}, "
            f"ниже сайта {below}/{len(full)}"
        )
    if thin:
        print(
            f"  проходные баллы (вердикт сайта почти пуст): {len(thin)} направлений "
            f"исключены из метрики — сайт отметил проходящими меньше "
            f"{int(_ORACLE_FULL_RATIO * 100)}% мест, его «проходной балл» там не порог, "
            "а балл одного-двух отмеченных"
        )


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
        print(
            "  прогноз: конкурсные списки есть, оракул сайта недоступен "
            "(вуз не отметил ни одного проходящего) — сверять не с чем"
        )
        return True
    robot = check.robot_title or "никуда не проходит"
    site = check.site_title or "никуда не проходит"
    if check.status == "match":
        print(f"  прогноз: совпал с сайтом — {robot}")
        return True
    if check.status == "boundary":
        print(
            f"  прогноз: ГРАНИЦА — робот: {robot} | сайт-порог: {site}; "
            "балл в точности равен проходному, при равном балле проходит не каждый"
        )
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

        if university not in AUDITED_UNIVERSITIES:
            # Не поломка, а решение спеки: у Политеха нет независимого источника
            # мест — робот и берёт, и мог бы сверять только с одной и той же
            # страницей fio_list_curl.php, сверка была бы круговой. Это не
            # проблема, поэтому в problems не идёт.
            print(f"  {MARKS['no_oracle']} оракула мест для «{university}» нет по замыслу — сверять не с чем")
        else:
            try:
                audits = audit_university(university, programs)
            except Exception as exc:  # noqa: BLE001
                print(f"  оракул мест недоступен: {exc}")
                problems += 1
            else:
                _print_seats(audits)
                problems += sum(1 for audit in audits if audit.is_problem)

        _print_transfers(programs)
        _print_cutoffs(university, programs)
        if not _print_paid(university, programs):
            problems += 1
        if not _print_placement(university):
            problems += 1

    print(f"\nИтого проблем: {problems}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
