from __future__ import annotations

from dataclasses import replace

from .models import ProgramConfig, ProgramResult
from .parsers.utils import extract_degree_id



def _degree_id(program: ProgramConfig) -> str:
    return str(program.parser_meta.get("degree_id") or extract_degree_id(program.list_url))


def _merged_program_label(programs: list[ProgramConfig]) -> str:
    names = [program.program.replace("\n", " / ").strip() or program.competition_group.strip() for program in programs]
    unique = list(dict.fromkeys(name for name in names if name))
    if len(unique) == 1:
        return unique[0]
    count = len(unique)
    if count % 10 == 1 and count % 100 != 11:
        suffix = "программа"
    elif count % 10 in {2, 3, 4} and count % 100 not in {12, 13, 14}:
        suffix = "программы"
    else:
        suffix = "программ"
    return f"Общий конкурс ({count} {suffix})"


def fetch_miit_with_cache(
    program: ProgramConfig,
    parser,
    cache: dict[str, ProgramResult],
) -> ProgramResult:
    degree = _degree_id(program)
    if degree in cache:
        cached = cache[degree]
        return ProgramResult(
            program=program,
            applicants=list(cached.applicants),
            fetched_at=cached.fetched_at,
            error=cached.error,
            source_places=cached.source_places,
        )

    result = parser.fetch(program)
    cache[degree] = result
    return result


def merge_rut_programs(
    rut_entries: list[tuple[ProgramConfig, ProgramResult]],
) -> list[tuple[ProgramConfig, ProgramResult]]:
    grouped: dict[str, list[tuple[ProgramConfig, ProgramResult]]] = {}
    for program, result in rut_entries:
        grouped.setdefault(_degree_id(program), []).append((program, result))

    merged: list[tuple[ProgramConfig, ProgramResult]] = []
    for degree in sorted(grouped):
        items = grouped[degree]
        programs = [program for program, _ in items]

        if len(items) == 1:
            merged.append(items[0])
            continue

        successful = [(program, result) for program, result in items if not result.error and result.applicants]
        if not successful:
            program, result = items[0]
            merged_program = replace(
                program,
                program=_merged_program_label(programs),
                budget_places=sum(item.budget_places for item in programs),
            )
            merged.append((merged_program, result))
            continue

        primary_program, primary_result = max(successful, key=lambda item: item[0].budget_places)
        total_places = sum(program.budget_places for program, _ in successful)
        merged_program = replace(
            primary_program,
            program=_merged_program_label(programs),
            budget_places=total_places,
        )
        merged.append((merged_program, primary_result))

    return merged
