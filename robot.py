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
    args = parser.parse_args()

    university = args.university
    parser_name = SUPPORTED_UNIVERSITIES.get(university)
    if parser_name is None:
        print(f"Робот пока не поддерживает вуз «{university}»", file=sys.stderr)
        return 1

    settings = load_robot_config()

    result = run_robot_simulation(
        university,
        settings,
        use_cache=not args.no_cache,
    )
    print(format_robot_result(result))
    return 1 if result.error else 0


if __name__ == "__main__":
    raise SystemExit(main())
