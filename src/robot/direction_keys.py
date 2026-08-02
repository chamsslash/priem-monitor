from __future__ import annotations

from ..models import ProgramConfig

# Официальный код ОКСО по названию конкурсной группы (стандарт единый по
# стране, не зависит от вуза) — проверено живьём в этой сессии: МИРЭА
# (programSubjectTitle из их API), МЭИ (колонка «Направление подготовки» в
# таблице КЦП), СТАНКИН (STANKIN_PROGRAMS в src/parsers/remaining.py).
# «Прикладная математика» (01.03.04) и «Прикладная математика и
# информатика» (01.03.02) — официально РАЗНЫЕ специальности, не путать.
OKSO_CODES: dict[str, str] = {
    "Информатика и вычислительная техника": "09.03.01",
    "Информационные системы и технологии": "09.03.02",
    "Прикладная информатика": "09.03.03",
    "Программная инженерия": "09.03.04",
    "Прикладная математика и информатика": "01.03.02",
    "Прикладная математика": "01.03.04",
    "Фундаментальная информатика и информационные технологии": "02.03.02",
}


# Точечные переопределения по id программы (config/programs.json) — либо
# конкретный ПРОФИЛЬ внутри направления имеет собственный уточняющий код
# (СТАНКИН, суффикс .NN — проверено через STANKIN_PROGRAMS в
# src/parsers/remaining.py), либо у разных вузов одно и то же название
# конкурсной группы означает РАЗНЫЕ официальные коды ОКСО (проверено живьём
# на pk.mpei.ru: id35 «ИТНО. Информационные системы и технологии» относится
# к 09.03.01, а не к 09.03.02, как у СТАНКИНской группы с тем же названием).
OKSO_CODE_OVERRIDES: dict[int, str] = {
    35: "09.03.01",
    47: "09.03.01.01",
    55: "09.03.03.01",
    56: "09.03.03.02",
    62: "09.03.02.01",
}


def okso_code_for_program(program: ProgramConfig) -> str | None:
    if program.id in OKSO_CODE_OVERRIDES:
        return OKSO_CODE_OVERRIDES[program.id]
    return OKSO_CODES.get(program.competition_group.strip())


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
