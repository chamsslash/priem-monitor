from __future__ import annotations

import fcntl
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "logs" / "update.log"
LOCK = ROOT / "logs" / "update.lock"


class UpdateStatus(str, Enum):
    SUCCESS = "success"
    ALREADY_RUNNING = "already_running"
    FAILED = "failed"


@dataclass(frozen=True)
class UpdateResult:
    status: UpdateStatus
    return_code: int = 0
    message: str = ""


def _append_log(line: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as log:
        log.write(line)


def run_update() -> UpdateResult:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = open(LOCK, "w", encoding="utf-8")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return UpdateResult(
            status=UpdateStatus.ALREADY_RUNNING,
            message="Обновление уже выполняется. Подождите пару минут.",
        )

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S %z")
    _append_log(f"=== {stamp} ===\n")

    result = subprocess.run(
        [sys.executable, str(ROOT / "update.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    if result.stdout:
        _append_log(result.stdout)
        if not result.stdout.endswith("\n"):
            _append_log("\n")

    if result.stderr:
        _append_log(result.stderr)
        if not result.stderr.endswith("\n"):
            _append_log("\n")

    if result.returncode == 0:
        return UpdateResult(
            status=UpdateStatus.SUCCESS,
            return_code=0,
            message=result.stdout.strip() or "Обновление завершено.",
        )

    return UpdateResult(
        status=UpdateStatus.FAILED,
        return_code=result.returncode,
        message=result.stderr.strip() or result.stdout.strip() or "Ошибка обновления.",
    )
