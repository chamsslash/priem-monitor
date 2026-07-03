from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config_loader import load_programs
from src.export_config import export_programs_from_xlsx
from src.service import refresh_all


def main() -> None:
    parser = argparse.ArgumentParser(description="Обновление конкурсных списков")
    parser.add_argument("--export-xlsx", action="store_true", help="Пересобрать config/programs.json из ВУЗы.xlsx")
    args = parser.parse_args()

    if args.export_xlsx:
        export_programs_from_xlsx(ROOT / "ВУЗы.xlsx", ROOT / "config" / "programs.json")
        print("Экспортирован config/programs.json")

    programs = load_programs()
    rows = refresh_all(programs)
    ok = [row for row in rows if not row.error]
    failed = [row for row in rows if row.error]
    print(f"Обновлено: {len(ok)} / {len(rows)}")
    for row in failed:
        print(f"[{row.parser}] {row.university} — {row.program[:50]}: {row.error}")


if __name__ == "__main__":
    main()
