from __future__ import annotations

from dataclasses import dataclass, field

from .priorities import get_saved_priority_keys, parse_priority_ids

DEFAULT_UNIVERSITY = "МИРЭА"
DEFAULT_PARSER = "mirea"
TELEGRAM_BUTTON_LIMIT = 64

# (chat_id, university) -> (draft priority_keys, ЗАМОРОЖЕННЫЙ на момент начала
# сессии редактирования список направлений). options фиксируются здесь же —
# не только priority_keys: callback_data кнопок кодирует ПОЗИЦИЮ в options
# (см. build_priority_keyboard), а RefreshWorker пересобирает пул кэша каждые
# несколько минут в фоне. Без заморозки два нажатия в одной открытой сессии
# редактирования могли бы увидеть РАЗНЫЙ options (сайт вуза не гарантирует
# неизменный порядок конкурсного списка между пересборками) — тогда индекс из
# старой клавиатуры резолвился бы в другое направление, чем то, что человек
# видел на экране, и нажатие тихо переставляло бы не то. Замороженный список
# живёт до explicit save/cancel (clear_priority_session).
_priority_sessions: dict[tuple[int, str], tuple[list[str], list[UserProgramOption]]] = {}


@dataclass
class UserProgramOption:
    """Одно направление пользователя, как оно реально стоит в его конкурсном
    списке — key берём из RobotProgram.key (ключ пула), а не program_id из
    config/programs.json: у большинства направлений пользователя id в
    конфиге просто нет, они там никогда не были описаны."""

    key: str
    title: str


@dataclass
class PriorityEditorState:
    university: str = DEFAULT_UNIVERSITY
    parser: str = DEFAULT_PARSER
    priority_keys: list[str] = field(default_factory=list)
    options: list[UserProgramOption] = field(default_factory=list)

    @property
    def options_by_key(self) -> dict[str, UserProgramOption]:
        return {option.key: option for option in self.options}


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
    from ..telegram_users import build_robot_settings, get_user_code, robot_config_path
    from .simulator import run_robot_simulation
    from .universities import SUPPORTED_UNIVERSITIES

    if parser is None:
        parser = SUPPORTED_UNIVERSITIES.get(university, DEFAULT_PARSER)

    session_key = _session_key(chat_id, university)
    session = _priority_sessions.get(session_key)
    if session is not None:
        # Сессия редактирования уже открыта — отдаём ЗАМОРОЖЕННЫЙ на её
        # начало options, а не свежий пересчёт из пула. build_priority_keyboard
        # кодирует в callback_data ПОЗИЦИЮ в options; если между двумя
        # нажатиями в одной сессии RefreshWorker пересоберёт кэш этого вуза
        # (циклы идут минутами, пользователь вполне может столько сидеть с
        # открытой клавиатурой) и порядок конкурсного списка на сайте
        # изменится, свежий пересчёт молча подсунул бы под старый индекс
        # ДРУГОЕ направление — нажатие переставило бы не то, что человек видел.
        priority_keys, options = session
        return PriorityEditorState(
            university=university,
            parser=parser,
            priority_keys=list(priority_keys),
            options=list(options),
        )

    options: list[UserProgramOption] = []
    code = get_user_code(chat_id)
    if code:
        # Без priority_ids: build_robot_settings строит настройки с нуля и не
        # тащит сохранённый порядок (см. её докстрока), поэтому
        # result.user_programs — это направления РОВНО в том порядке, в
        # котором человек подавал их на сайте вуза. Редактор должен листать
        # эту настоящую вселенную направлений целиком, а не то, что было
        # переставлено раньше.
        # stale_ok=True: обработчик команд Telegram крутится в одном потоке с
        # getUpdates — сетевой поход здесь подвесил бы бота всем чатам разом,
        # пересборку заказывает RefreshWorker отдельно.
        settings = build_robot_settings(code, university)
        result = run_robot_simulation(university, settings=settings, stale_ok=True)
        options = [
            UserProgramOption(key=state.program_key, title=state.title)
            for state in result.user_programs
        ]

    config_path = robot_config_path(chat_id)
    # Личного файла может ещё не быть на диске (пользователь не завершил
    # регистрацию по коду) — в этом случае get_saved_priority_keys ушёл бы
    # в load_raw_config() и откатился на config/robot.example.json, где
    # лежат настоящие Димины приоритеты. Читаем сохранённый порядок только
    # если личный файл реально существует, иначе считаем, что порядок не
    # задан (используется реальный, поданный на сайте).
    saved = get_saved_priority_keys(university, path=config_path) if config_path.exists() else []
    # Чужой/устаревший ключ (в т.ч. числовой id старого формата хранения —
    # int никогда не равен str, даже при совпадающих цифрах) просто не
    # попадёт в available и молча отсеется здесь.
    available = {option.key for option in options}
    priority_keys = [item for item in saved if item in available]
    return PriorityEditorState(
        university=university,
        parser=parser,
        priority_keys=list(priority_keys),
        options=options,
    )


def save_priority_editor(chat_id: int, state: PriorityEditorState) -> None:
    # Замораживает options ВМЕСТЕ с priority_keys — см. комментарий у
    # _priority_sessions. Вызывается и на старте сессии (первая заморозка), и
    # после каждого tog/up/dn (persist черновика) — во втором случае state.options
    # уже сам пришёл из замороженной сессии (load_priority_editor вернул её),
    # так что здесь просто перекладывается тот же список, а не пересчитывается.
    _priority_sessions[_session_key(chat_id, state.university)] = (
        list(state.priority_keys),
        list(state.options),
    )


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

    if not state.priority_keys:
        lines.append("— не заданы —")
    else:
        for rank, key in enumerate(state.priority_keys, start=1):
            option = state.options_by_key.get(key)
            title = option.title if option else f"ключ {key}"
            lines.append(f"{rank}. {title}")

    excluded = [option for option in state.options if option.key not in state.priority_keys]
    if excluded:
        lines.append("")
        lines.append("Исключены из расчёта:")
        for option in excluded:
            lines.append(f"• {option.title}")

    return "\n".join(lines)


def build_priority_keyboard(state: PriorityEditorState) -> dict:
    buttons: list[list[dict[str, str]]] = []
    priority_set = set(state.priority_keys)

    # callback_data кодирует ПОЗИЦИЮ в state.options, а не сам ключ: у ФА
    # ключ — это целый заголовок конкурсного списка (десятки символов),
    # он не влезает в лимит Telegram на callback_data (64 байта), да и у
    # остальных вузов ключ — это внутренний id, незачем гонять его в оба
    # конца. Обработчик в scripts/telegram_bot.py переводит индекс обратно
    # в ключ через state.options сразу после клика, в рамках того же рендера.
    for index, option in enumerate(state.options):
        if option.key in priority_set:
            rank = state.priority_keys.index(option.key) + 1
            prefix = f"✓ {rank}. "
            buttons.append(
                [
                    {"text": "↑", "callback_data": f"prio:up:{index}"},
                    {"text": "↓", "callback_data": f"prio:dn:{index}"},
                    {
                        "text": prefix + _button_label(option.title, prefix),
                        "callback_data": f"prio:tog:{index}",
                    },
                ]
            )
        else:
            prefix = "○ "
            buttons.append(
                [
                    {
                        "text": prefix + _button_label(option.title, prefix),
                        "callback_data": f"prio:tog:{index}",
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


def toggle_program(state: PriorityEditorState, key: str) -> None:
    if key in state.priority_keys:
        state.priority_keys.remove(key)
    else:
        state.priority_keys.append(key)


def move_program(state: PriorityEditorState, key: str, direction: str) -> bool:
    if key not in state.priority_keys:
        return False
    index = state.priority_keys.index(key)
    if direction == "up" and index > 0:
        state.priority_keys[index], state.priority_keys[index - 1] = (
            state.priority_keys[index - 1],
            state.priority_keys[index],
        )
        return True
    if direction == "down" and index < len(state.priority_keys) - 1:
        state.priority_keys[index], state.priority_keys[index + 1] = (
            state.priority_keys[index + 1],
            state.priority_keys[index],
        )
        return True
    return False


def try_parse_priority_command(
    text: str,
    chat_id: int,
    *,
    default_university: str = DEFAULT_UNIVERSITY,
    supported_universities: set[str] | None = None,
) -> tuple[str, list[str]] | None:
    parts = text.strip().split()
    if len(parts) < 2:
        return None

    from .universities import match_university_prefix

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

    # Ручной ввод адресует направление НОМЕРОМ позиции в реальном списке —
    # тем же способом, что и /робот (нумерует «Учитываются приоритеты») и
    # /конкуренты <вуз> <номер приоритета>. Ключ пула пользователю нигде не
    # показывается (а у ФА это ещё и целый заголовок списка, набрать его
    # вручную нереально), поэтому набирать можно только позиции.
    state = load_priority_editor(chat_id, university=university)
    if not state.options:
        raise ValueError(f"Нет направлений для «{university}» — код не найден в списках вуза")
    positions = parse_priority_ids(" ".join(id_parts), set(range(1, len(state.options) + 1)))
    return university, [state.options[position - 1].key for position in positions]


def university_from_message(text: str) -> str:
    first = (text or "").split("\n", 1)[0]
    prefix = "📋 Приоритеты "
    if first.startswith(prefix):
        name = first[len(prefix) :].strip()
        if name:
            return name
    return DEFAULT_UNIVERSITY


def format_saved_confirmation(state: PriorityEditorState) -> str:
    return "✅ Приоритеты сохранены.\n\n" + format_priority_view(state, editing=False)
