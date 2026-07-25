from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROBOT_JSON = ROOT / "config" / "robot.json"
EXAMPLE_ROBOT_JSON = ROOT / "config" / "robot.example.json"


@dataclass
class RobotSettings:
    dima_code: str
    dima_score: int
    dima_consent: bool
    require_consent: bool
    universities: dict[str, dict]


def load_robot_config(path: Path | None = None) -> RobotSettings:
    config_path = path or DEFAULT_ROBOT_JSON
    if not config_path.exists():
        config_path = EXAMPLE_ROBOT_JSON
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    dima = raw.get("dima", {})
    simulation = raw.get("simulation", {})
    return RobotSettings(
        dima_code=str(dima.get("code", "DIMA")),
        dima_score=int(dima.get("score", 259)),
        dima_consent=bool(dima.get("consent", True)),
        require_consent=bool(simulation.get("require_consent", True)),
        universities=raw.get("universities", {}),
    )
