from __future__ import annotations

from ..models import ProgramConfig


def _program_label(program: ProgramConfig) -> str:
    return program.program.replace("\n", " / ").strip() or program.competition_group.strip()


def program_display_name(program: ProgramConfig) -> str:
    return _program_label(program)
