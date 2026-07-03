from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def run_update() -> int:
    result = subprocess.run(
        [sys.executable, str(ROOT / "update.py")],
        cwd=ROOT,
        check=False,
    )
    return result.returncode


def main() -> None:
    parser = argparse.ArgumentParser(description="Периодический запуск update.py")
    parser.add_argument(
        "--interval",
        type=int,
        default=7200,
        help="Интервал между обновлениями в секундах (по умолчанию 7200 = 2 часа)",
    )
    parser.add_argument("--once", action="store_true", help="Выполнить одно обновление и выйти")
    args = parser.parse_args()

    if args.interval < 300:
        raise SystemExit("Интервал должен быть не меньше 300 секунд (5 минут).")

    if args.once:
        raise SystemExit(run_update())

    print(f"Планировщик запущен. Интервал: {args.interval} сек. Ctrl+C для остановки.")
    while True:
        started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{started}] Обновление...")
        code = run_update()
        print(f"[{started}] Готово, код выхода: {code}")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
