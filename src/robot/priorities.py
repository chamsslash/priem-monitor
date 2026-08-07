from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from ..config_loader import load_programs
from ..models import ProgramConfig

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROBOT_JSON = ROOT / "config" / "robot.json"
EXAMPLE_ROBOT_JSON = ROOT / "config" / "robot.example.json"


@dataclass
class ProgramOption:
    program_id: int
    title: str
    direction_code: str


def _program_label(program: ProgramConfig) -> str:
    return program.program.replace("\n", " / ").strip() or program.competition_group.strip()


def program_display_name(program: ProgramConfig) -> str:
    return _program_label(program)


def list_university_programs(university: str, parser: str) -> list[ProgramOption]:
    programs = [
        program
        for program in load_programs()
        if program.university == university and program.parser == parser
    ]
    return [
        ProgramOption(
            program_id=program.id,
            title=_program_label(program),
            direction_code=program.direction_code,
        )
        for program in programs
    ]


def parse_priority_ids(raw: str, available_ids: set[int]) -> list[int]:
    ids: list[int] = []
    for chunk in re.split(r"[\s,;]+", raw.strip()):
        if not chunk:
            continue
        program_id = int(chunk)
        if program_id not in available_ids:
            raise ValueError(f"Неизвестный program_id: {program_id}")
        if program_id not in ids:
            ids.append(program_id)
    if not ids:
        raise ValueError("Список приоритетов пуст")
    return ids


def load_raw_config(path: Path | None = None) -> dict:
    config_path = path or DEFAULT_ROBOT_JSON
    if not config_path.exists():
        config_path = EXAMPLE_ROBOT_JSON
    return json.loads(config_path.read_text(encoding="utf-8"))


def get_saved_priority_keys(university: str, path: Path | None = None) -> list[str]:
    """Возвращает сохранённый порядок направлений — ключи RobotProgram.key.

    Старый формат хранил числовой id из config/programs.json (до этой задачи).
    Такие записи НЕ конвертируются: читаем их как есть (без принудительного
    str()/int()), и caller сверяет со множеством реальных ключей пула — число
    там просто не совпадёт ни с одной строкой и молча отсеется (см.
    interactive_set_priorities и telegram_priorities.load_priority_editor).
    Принудительный int(item), который был здесь раньше, уронил бы чтение на
    современном значении вроде title ФА, которое в int не превращается."""
    raw = load_raw_config(path)
    university_cfg = raw.get("universities", {}).get(university, {})
    return list(university_cfg.get("dima_priorities", []))


def save_priority_keys(university: str, priority_keys: list[str], path: Path | None = None) -> Path:
    config_path = path or DEFAULT_ROBOT_JSON
    if not config_path.exists():
        raw = json.loads(EXAMPLE_ROBOT_JSON.read_text(encoding="utf-8"))
    else:
        raw = json.loads(config_path.read_text(encoding="utf-8"))

    universities = raw.setdefault("universities", {})
    university_cfg = universities.setdefault(university, {"enabled": True})
    university_cfg["dima_priorities"] = priority_keys
    university_cfg["enabled"] = True
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return config_path


def format_program_list(university: str, parser: str, priority_ids: list[int] | None = None) -> str:
    options = list_university_programs(university, parser)
    if not options:
        return f"Нет отслеживаемых программ для «{university}»."

    priority_ids = priority_ids or []
    priority_set = set(priority_ids)
    lines = [f"Программы {university}:"]
    for option in options:
        if priority_ids:
            if option.program_id in priority_set:
                rank = priority_ids.index(option.program_id) + 1
                mark = f"приоритет {rank}"
            else:
                mark = "исключена"
        else:
            mark = "не в приоритетах"
        lines.append(f"• [{option.program_id}] {option.title} — {mark}")
    if priority_ids:
        lines.append("")
        lines.append("Текущий порядок: " + " → ".join(str(item) for item in priority_ids))
    return "\n".join(lines)


def interactive_set_priorities(university: str, parser: str) -> list[int]:
    options = list_university_programs(university, parser)
    if not options:
        raise ValueError(f"Нет отслеживаемых программ для «{university}»")

    available = {option.program_id for option in options}
    saved = [item for item in get_saved_priority_keys(university) if item in available]

    print(f"\nПрограммы {university} (только выбранные попадут в расчёт):\n")
    for option in options:
        print(f"  [{option.program_id}] {option.title}")
    print()
    if saved:
        print("Сейчас:", " → ".join(str(item) for item in saved))
    print("Введите id через пробел в порядке приоритета.")
    print("Программы, которых нет в списке, будут исключены.\n")

    while True:
        raw = input("Приоритеты> ").strip()
        if not raw and saved:
            return saved
        try:
            return parse_priority_ids(raw, available)
        except ValueError as exc:
            print(f"Ошибка: {exc}")
