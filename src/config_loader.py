from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import ProgramConfig

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROGRAMS_JSON = ROOT / "config" / "programs.json"
DEFAULT_FILTERS_JSON = ROOT / "config" / "filters.json"
DEFAULT_XLSX = ROOT / "ВУЗы.xlsx"


def load_filters(path: Path | None = None) -> dict[str, dict[str, Any]]:
    filters_path = path or DEFAULT_FILTERS_JSON
    if not filters_path.exists():
        return {}
    return json.loads(filters_path.read_text(encoding="utf-8"))


def load_programs(
    json_path: Path | None = None,
    filters_path: Path | None = None,
) -> list[ProgramConfig]:
    programs_path = json_path or DEFAULT_PROGRAMS_JSON
    if not programs_path.exists() and DEFAULT_XLSX.exists():
        from .export_config import export_programs_from_xlsx

        export_programs_from_xlsx(DEFAULT_XLSX, programs_path)

    raw = json.loads(programs_path.read_text(encoding="utf-8"))
    filters = load_filters(filters_path)
    programs: list[ProgramConfig] = []
    for item in raw:
        program_id = str(item["id"])
        programs.append(
            ProgramConfig(
                id=item["id"],
                university=item["university"],
                direction_code=str(item.get("direction_code", "")),
                competition_group=item.get("competition_group", ""),
                program=item.get("program", ""),
                passing_score_2025=float(item.get("passing_score_2025") or 0),
                dima_score=int(item.get("dima_score") or 0),
                budget_places=int(item.get("budget_places") or 0),
                study_plan_url=item.get("study_plan_url", ""),
                list_url=item.get("list_url", ""),
                parser=item.get("parser", "unknown"),
                parser_meta=item.get("parser_meta", {}),
                needs_filter=bool(item.get("needs_filter")),
                filter_rules=filters.get(program_id, item.get("filter_rules", {})),
            )
        )
    return programs
