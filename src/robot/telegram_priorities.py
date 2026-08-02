from __future__ import annotations

from dataclasses import dataclass, field

from .priorities import (
    ProgramOption,
    format_program_list,
    get_saved_priority_ids,
    list_university_programs,
    parse_priority_ids,
    save_priority_ids,
)

DEFAULT_UNIVERSITY = "МИРЭА"
DEFAULT_PARSER = "mirea"
TELEGRAM_BUTTON_LIMIT = 64

# (chat_id, university) -> draft priority order during inline editing
_priority_sessions: dict[tuple[int, str], list[int]] = {}


@dataclass
class PriorityEditorState:
    university: str = DEFAULT_UNIVERSITY
    parser: str = DEFAULT_PARSER
    priority_ids: list[int] = field(default_factory=list)
    options: list[ProgramOption] = field(default_factory=list)

    @property
    def options_by_id(self) -> dict[int, ProgramOption]:
        return {option.program_id: option for option in self.options}


def _button_label(title: str, prefix: str = "") -> str:
    limit = TELEGRAM_BUTTON_LIMIT - len(prefix)
    if len(title) <= limit:
        return title
    return title[: max(limit - 1, 1)] + "…"


def _session_key(chat_id: int, university: str) -> tuple[int, str]:
    return chat_id, university


def load_priority_editor(
    chat_id: int,
    university: str = DEFAULT_UNIVERSITY,
    parser: str | None = None,
) -> PriorityEditorState:
    from ..telegram_users import robot_config_path

    if parser is None:
        from .universities import SUPPORTED_UNIVERSITIES

        parser = SUPPORTED_UNIVERSITIES.get(university, DEFAULT_PARSER)
    options = list_university_programs(university, parser)
    config_path = robot_config_path(chat_id)
    # Личного файла может ещё не быть на диске (пользователь не завершил
    # регистрацию по коду) — в этом случае get_saved_priority_ids ушёл бы
    # в load_raw_config() и откатился на config/robot.example.json, где
    # лежат настоящие Димины приоритеты. Читаем сохранённые приоритеты
    # только если личный файл реально существует, иначе считаем, что
    # приоритетов пока нет.
    saved = get_saved_priority_ids(university, path=config_path) if config_path.exists() else []
    available = {option.program_id for option in options}
    session_key = _session_key(chat_id, university)
    priority_ids = _priority_sessions.get(session_key) or [item for item in saved if item in available]
    return PriorityEditorState(
        university=university,
        parser=parser,
        priority_ids=list(priority_ids),
        options=options,
    )


def save_priority_editor(chat_id: int, state: PriorityEditorState) -> None:
    _priority_sessions[_session_key(chat_id, state.university)] = list(state.priority_ids)


def clear_priority_session(chat_id: int, university: str) -> None:
    _priority_sessions.pop(_session_key(chat_id, university), None)


def format_priority_view(state: PriorityEditorState, *, editing: bool = False) -> str:
    if not state.options:
        return f"Нет отслеживаемых программ для «{state.university}»."

    lines = [f"📋 Приоритеты {state.university}"]
    if editing:
        lines.append("Нажимайте программу, чтобы включить или убрать.")
        lines.append("↑↓ — поменять порядок. Только выбранные попадут в /робот.")
    else:
        lines.append("Порядок зачисления в роботе:")

    if not state.priority_ids:
        lines.append("— не заданы —")
    else:
        for rank, program_id in enumerate(state.priority_ids, start=1):
            option = state.options_by_id.get(program_id)
            title = option.title if option else f"id {program_id}"
            lines.append(f"{rank}. {title}")

    excluded = [option for option in state.options if option.program_id not in state.priority_ids]
    if excluded:
        lines.append("")
        lines.append("Исключены из расчёта:")
        for option in excluded:
            lines.append(f"• {option.title}")

    return "\n".join(lines)


def build_priority_keyboard(state: PriorityEditorState) -> dict:
    buttons: list[list[dict[str, str]]] = []
    priority_set = set(state.priority_ids)

    for option in state.options:
        program_id = option.program_id
        if program_id in priority_set:
            rank = state.priority_ids.index(program_id) + 1
            prefix = f"✓ {rank}. "
            buttons.append(
                [
                    {"text": "↑", "callback_data": f"prio:up:{program_id}"},
                    {"text": "↓", "callback_data": f"prio:dn:{program_id}"},
                    {
                        "text": prefix + _button_label(option.title, prefix),
                        "callback_data": f"prio:tog:{program_id}",
                    },
                ]
            )
        else:
            prefix = "○ "
            buttons.append(
                [
                    {
                        "text": prefix + _button_label(option.title, prefix),
                        "callback_data": f"prio:tog:{program_id}",
                    }
                ]
            )

    buttons.append(
        [
            {"text": "💾 Сохранить", "callback_data": "prio:save"},
            {"text": "↩️ Отмена", "callback_data": "prio:cancel"},
        ]
    )
    return {"inline_keyboard": buttons}


def build_priority_view_keyboard() -> dict:
    return {"inline_keyboard": [[{"text": "✏️ Изменить", "callback_data": "prio:edit"}]]}


def toggle_program(state: PriorityEditorState, program_id: int) -> None:
    if program_id in state.priority_ids:
        state.priority_ids.remove(program_id)
    else:
        state.priority_ids.append(program_id)


def move_program(state: PriorityEditorState, program_id: int, direction: str) -> bool:
    if program_id not in state.priority_ids:
        return False
    index = state.priority_ids.index(program_id)
    if direction == "up" and index > 0:
        state.priority_ids[index], state.priority_ids[index - 1] = (
            state.priority_ids[index - 1],
            state.priority_ids[index],
        )
        return True
    if direction == "down" and index < len(state.priority_ids) - 1:
        state.priority_ids[index], state.priority_ids[index + 1] = (
            state.priority_ids[index + 1],
            state.priority_ids[index],
        )
        return True
    return False


def try_parse_priority_command(
    text: str,
    *,
    default_university: str = DEFAULT_UNIVERSITY,
    supported_universities: set[str] | None = None,
) -> tuple[str, list[int]] | None:
    parts = text.strip().split()
    if len(parts) < 2:
        return None

    from .universities import SUPPORTED_UNIVERSITIES, match_university_prefix

    supported = supported_universities or set()
    university = default_university
    id_parts = parts[1:]

    matched, consumed = match_university_prefix(parts[1:], supported)
    if matched is not None:
        university = matched
        id_parts = parts[1 + consumed :]
        if not id_parts:
            return university, []

    if not id_parts:
        return None

    parser_name = SUPPORTED_UNIVERSITIES.get(university)
    if parser_name is None:
        raise ValueError(f"Робот не поддерживает «{university}»")
    available = {option.program_id for option in list_university_programs(university, parser_name)}
    return university, parse_priority_ids(" ".join(id_parts), available)


def university_from_message(text: str) -> str:
    first = (text or "").split("\n", 1)[0]
    prefix = "📋 Приоритеты "
    if first.startswith(prefix):
        name = first[len(prefix) :].strip()
        if name:
            return name
    return DEFAULT_UNIVERSITY


def format_saved_confirmation(state: PriorityEditorState) -> str:
    return "✅ Приоритеты сохранены.\n\n" + format_program_list(
        state.university,
        state.parser,
        state.priority_ids,
    )
