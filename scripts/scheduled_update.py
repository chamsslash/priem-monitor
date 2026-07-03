#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.run_update import UpdateStatus, run_update
from src.service import load_results
from src.telegram_notify import push_update


def main() -> int:
    old_results = load_results()
    result = run_update()
    if result.status == UpdateStatus.ALREADY_RUNNING:
        return 0
    if result.status != UpdateStatus.SUCCESS:
        return result.return_code or 1

    new_results = load_results()
    try:
        push_update(old_results, new_results)
    except Exception as exc:
        print(f"Telegram push failed: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
