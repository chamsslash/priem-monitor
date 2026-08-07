#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.robot.config import load_robot_config
from src.robot.format import format_robot_result
from src.robot.priorities import (
    format_program_list,
    get_saved_priority_keys,
    interactive_set_priorities,
    list_university_programs,
    parse_priority_ids,
    save_priority_keys,
)
from src.robot.simulator import run_robot_simulation
from src.robot.universities import SUPPORTED_UNIVERSITIES


def main() -> int:
    parser = argparse.ArgumentParser(description="Симуляция робота зачисления по приоритетам")
    parser.add_argument(
        "university",
        nargs="?",
        default="МИРЭА",
        help=f"Вуз ({', '.join(sorted(SUPPORTED_UNIVERSITIES))})",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Перезагрузить списки с сайта, игнорируя кэш",
    )
    parser.add_argument(
        "--priorities",
        metavar="IDS",
        help="Приоритеты для этого запуска: id через запятую или пробел (остальные исключаются)",
    )
    parser.add_argument(
        "--set-priorities",
        action="store_true",
        help="Интерактивно расставить приоритеты перед расчётом",
    )
    parser.add_argument(
        "--save-priorities",
        action="store_true",
        help="Сохранить приоритеты из --priorities или --set-priorities в config/robot.json",
    )
    parser.add_argument(
        "--list-programs",
        action="store_true",
        help="Показать программы вуза и текущие приоритеты",
    )
    args = parser.parse_args()

    university = args.university
    parser_name = SUPPORTED_UNIVERSITIES.get(university)
    if parser_name is None:
        print(f"Робот пока не поддерживает вуз «{university}»", file=sys.stderr)
        return 1

    if args.list_programs:
        saved = get_saved_priority_keys(university)
        print(format_program_list(university, parser_name, saved))
        return 0

    priority_ids: list[int] | None = None

    if args.set_priorities:
        priority_ids = interactive_set_priorities(university, parser_name)
        if args.save_priorities or input("Сохранить в config/robot.json? [y/N] ").strip().lower() in {"y", "yes", "д", "да"}:
            path = save_priority_keys(university, priority_ids)
            print(f"Сохранено: {path}")
    elif args.priorities:
        available = {option.program_id for option in list_university_programs(university, parser_name)}
        try:
            priority_ids = parse_priority_ids(args.priorities, available)
        except ValueError as exc:
            print(f"Ошибка: {exc}", file=sys.stderr)
            return 1
        if args.save_priorities:
            path = save_priority_keys(university, priority_ids)
            print(f"Сохранено: {path}")
    else:
        saved = get_saved_priority_keys(university)
        if not saved:
            print("Приоритеты не заданы. Запустите с --set-priorities или --priorities.")
            print(format_program_list(university, parser_name))
            return 1

    settings = load_robot_config()
    result = run_robot_simulation(
        university,
        settings,
        use_cache=not args.no_cache,
        priority_ids=priority_ids,
    )
    print(format_robot_result(result))
    return 1 if result.error else 0


if __name__ == "__main__":
    raise SystemExit(main())
