from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config_loader import load_programs
from .models import ProgramConfig, ProgramResult, RankSnapshot
from .parsers.registry import get_parser
from .ranking import build_rank_snapshot
from .rut_merge import fetch_miit_with_cache, merge_rut_programs

ROOT = Path(__file__).resolve().parents[1]
RESULTS_PATH = ROOT / "data" / "latest_results.json"


@dataclass
class DashboardRow:
    program_id: int
    university: str
    mega_direction: str
    program: str
    budget_places: int
    dima_score: int
    rank_consent: int | None
    rank_priority1: int | None
    rank_consent_priority1: int | None
    status: str
    probability: float
    probability_label: str
    parser: str
    error: str | None
    list_url: str
    updated_at: str


def _mega_key(program: ProgramConfig) -> str:
    return f"{program.direction_code} {program.competition_group}".strip()


def build_dashboard_row(program: ProgramConfig, result: ProgramResult) -> DashboardRow:
    if result.error:
        return DashboardRow(
            program_id=program.id,
            university=program.university,
            mega_direction=_mega_key(program),
            program=program.program.replace("\n", " / ").strip() or program.competition_group.strip(),
            budget_places=program.budget_places,
            dima_score=program.dima_score,
            rank_consent=None,
            rank_priority1=None,
            rank_consent_priority1=None,
            status="unknown",
            probability=0.0,
            probability_label="нет данных",
            parser=program.parser,
            error=result.error,
            list_url=program.list_url,
            updated_at=result.fetched_at,
        )

    snapshot: RankSnapshot = build_rank_snapshot(program, result.applicants)
    return DashboardRow(
        program_id=program.id,
        university=program.university,
        mega_direction=_mega_key(program),
        program=program.program.replace("\n", " / ").strip() or program.competition_group.strip(),
        budget_places=program.budget_places,
        dima_score=program.dima_score,
        rank_consent=snapshot.rank_consent,
        rank_priority1=snapshot.rank_priority1,
        rank_consent_priority1=snapshot.rank_consent_priority1,
        status=snapshot.status,
        probability=snapshot.probability,
        probability_label=snapshot.probability_label,
        parser=program.parser,
        error=None,
        list_url=program.list_url,
        updated_at=snapshot.updated_at,
    )


def refresh_all(programs: list[ProgramConfig] | None = None) -> list[DashboardRow]:
    programs = programs or load_programs()
    rows: list[DashboardRow] = []
    rut_entries: list[tuple[ProgramConfig, ProgramResult]] = []
    miit_cache: dict[str, ProgramResult] = {}

    for program in programs:
        parser = get_parser(program.parser)
        if program.parser == "miit":
            result = fetch_miit_with_cache(program, parser, miit_cache)
            rut_entries.append((program, result))
            continue

        result = parser.fetch(program)
        rows.append(build_dashboard_row(program, result))

    for program, result in merge_rut_programs(rut_entries):
        rows.append(build_dashboard_row(program, result))

    rows.sort(key=lambda row: (row.university, row.mega_direction, row.program_id))
    save_results(rows)
    return rows


def save_results(rows: list[DashboardRow]) -> None:
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rows": [asdict(row) for row in rows],
    }
    RESULTS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_results() -> dict:
    if not RESULTS_PATH.exists():
        return {"generated_at": None, "rows": []}
    return json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
