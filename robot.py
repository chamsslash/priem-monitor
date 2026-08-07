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

    settings = load_robot_config()
    university_cfg = settings.universities.get(university, {})
    # dima_list_code включает «настоящий» путь (_resolve_dima_person в
    # simulator.py): направления и их порядок берутся из живого конкурсного
    # списка человека, а порядок приоритетов — это ключи RobotProgram.key
    # (см. _apply_priority_order), а не числовые id config/programs.json.
    # Все флаги ниже (--list-programs/--set-priorities/--priorities) работают
    # только с config-id — для такого вуза они молча ничего не делают: id и
    # ключ пула никогда не совпадут (int никогда не равен str), симуляция
    # тихо идёт по естественному порядку сайта, а отчёт печатается как ни в
    # чём не бывало. Переводить эти команды на ключи пула — за рамками
    # минимальной починки (нужен отдельный live-запрос по коду, как в
    # telegram_priorities.load_priority_editor), поэтому падаем с понятной
    # ошибкой вместо вранья.
    if university_cfg.get("dima_list_code") and (
        args.list_programs or args.set_priorities or args.priorities
    ):
        print(
            f"«{university}»: порядок приоритетов задаётся ключами направлений "
            "пула (реальный конкурсный список по dima_list_code), а не id "
            "config/programs.json — --list-programs/--set-priorities/--priorities "
            "здесь не применимы. Отредактируйте dima_priorities в config/robot.json "
            "вручную ключами направлений (RobotProgram.key) либо через "
            "/приоритет в Telegram-боте.",
            file=sys.stderr,
        )
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
