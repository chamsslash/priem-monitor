from __future__ import annotations

from ..models import ProgramConfig


def fa_list_title(program: ProgramConfig) -> str:
    program_name = program.program.replace("\n", " / ").strip() or program.competition_group.strip()
    return f"{program.competition_group}, Бакалавр, {program_name}, Очная"


def direction_key_for_program(program: ProgramConfig) -> str:
    if program.parser == "mirea":
        comp_id = program.parser_meta.get("comp_id")
        if not comp_id:
            raise ValueError(f"Не указан comp_id для программы id={program.id}")
        return str(comp_id)
    if program.parser == "fa":
        return fa_list_title(program)
    if program.parser in {"mpei", "stankin", "mospolytech"}:
        return str(program.id)
    raise ValueError(f"Робот не поддерживает parser={program.parser}")
