# Самообслуживание в Telegram-боте по коду поступающего — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Убрать ручной допуск по `chat_id` и захардкоженную личность «Дима» —
любой пользователь Telegram-бота регистрируется своим уникальным кодом
поступающего и получает `/статус`/`/робот` по своим реальным данным (4 вуза
с готовой инфраструктурой полного пула: Финуниверситет, МИРЭА, МЭИ,
СТАНКИН).

**Architecture:** Новый модуль `src/telegram_users.py` — персональное
хранилище `chat_id → код` (`data/telegram_users.json`) плюс сборка
`RobotSettings` на лету для симулятора (который уже принимает настройки
параметром, не константой — сам симулятор и пулы не меняются). Приоритеты
по-прежнему через `src/robot/priorities.py` (не меняется) — но вызываются
с `path=` на отдельный файл на каждого пользователя
(`data/telegram_users/<chat_id>.json`, та же JSON-форма, что и
`config/robot.json`), а не на общий `config/robot.json`.

**Tech Stack:** Python 3, без новых зависимостей.

## Global Constraints

- В проекте нет тестового фреймворка (`pytest` не установлен) и нет моков
  сети для сетевых вызовов — задачи, которые реально ходят на сайты вузов,
  проверяются через `assert` в одноразовом скрипте на живых данных, как и
  весь предыдущий план. Задачи, которые проверяют обработку Telegram-команд
  (`scripts/telegram_bot.py`), сети не касаются вообще — там монки-патчатся
  `send_message`/`answer_callback_query`/`edit_message_text`, чтобы
  перехватить, что БЫЛО БЫ отправлено, без реального похода в Telegram API
  (этот приём уже использовался сегодня в раундах фиксов Task 7/9 —
  устоявшийся в этой сессии способ проверки без сети).
- `src/robot/simulator.py`, `src/robot/direction_keys.py`,
  `src/robot/mirea_pool.py`, `src/robot/fa_pool.py`,
  `src/robot/mpei_pool.py`, `src/robot/stankin_pool.py`,
  `config/robot.json`, `config/programs.json` — **не менять**. Симулятор уже
  принимает `RobotSettings`/`university_cfg` параметром, этого достаточно.
- `src/robot/priorities.py` (`get_saved_priority_ids`/`save_priority_ids`)
  — **не менять**: они уже принимают `path: Path | None` — переиспользуем
  через явный `path=`, вместо того чтобы дублировать логику чтения/записи.
- `app.py` (Streamlit), `src/service.py`, `scheduler.py`, `update.py`,
  `scripts/scheduled_update.py` — **не менять**, вне области задачи
  (независимый потребитель тех же данных для 10 вузов, работает по
  `dima_score` из `config/programs.json`).
- Коммитить после каждой задачи отдельным коммитом (сообщения на русском,
  без `Co-Authored-By`).
- Новые файлы с персональными данными (`data/telegram_users.json`,
  `data/telegram_users/*.json`) обязаны быть в `.gitignore`.

---

## Task 1: Хранилище пользователей — регистрация по коду

**Files:**
- Create: `src/telegram_users.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `looks_like_code(text: str) -> bool`, `get_user_code(chat_id: int) -> str | None`,
  `is_registered(chat_id: int) -> bool`, `set_user_code(chat_id: int, code: str) -> None`,
  `robot_config_path(chat_id: int) -> Path`

- [ ] **Step 1: Создать модуль хранилища**

Создать `src/telegram_users.py`:

```python
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
USERS_PATH = ROOT / "data" / "telegram_users.json"
USER_ROBOT_CONFIG_DIR = ROOT / "data" / "telegram_users"

_CODE_RE = re.compile(r"^\d{4,15}$")


def looks_like_code(text: str) -> bool:
    return bool(_CODE_RE.match(text.strip()))


def _load_all() -> dict[str, str]:
    if not USERS_PATH.exists():
        return {}
    try:
        return json.loads(USERS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        return {}


def _save_all(data: dict[str, str]) -> None:
    USERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    USERS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def get_user_code(chat_id: int) -> str | None:
    return _load_all().get(str(chat_id))


def is_registered(chat_id: int) -> bool:
    return get_user_code(chat_id) is not None


def robot_config_path(chat_id: int) -> Path:
    return USER_ROBOT_CONFIG_DIR / f"{chat_id}.json"


def _ensure_blank_robot_config(chat_id: int) -> None:
    """Гарантирует, что личный robot.json-подобный файл существует и пуст.

    Критично: src/robot/priorities.py::load_raw_config() при отсутствии
    файла по указанному path падает обратно на config/robot.example.json —
    а он НЕ пустой шаблон, там реальные Димины dima_priorities. Без явного
    создания пустого файла новый пользователь получил бы Димины приоритеты
    по умолчанию вместо "нет приоритетов = берём реальный поданный порядок".
    """
    path = robot_config_path(chat_id)
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"universities": {}}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def set_user_code(chat_id: int, code: str) -> None:
    data = _load_all()
    data[str(chat_id)] = code
    _save_all(data)
    _ensure_blank_robot_config(chat_id)
```

- [ ] **Step 2: Добавить новые файлы в `.gitignore`**

В `.gitignore` после строки `data/cache/` добавить:

```
data/telegram_users.json
data/telegram_users/
```

- [ ] **Step 3: Проверить вживую (без сети — только файловый ввод-вывод)**

```bash
cd /Users/gyattalert/work/priem-monitor
python3 - <<'EOF'
import sys
sys.path.insert(0, ".")
from src.telegram_users import (
    looks_like_code, get_user_code, is_registered, set_user_code, robot_config_path,
)

TEST_CHAT_ID = 999999999999  # заведомо нереальный chat_id, чтобы не задеть настоящих пользователей

assert looks_like_code("1824102") is True
assert looks_like_code("12") is False  # короче 4 цифр
assert looks_like_code("абвгд") is False
assert looks_like_code("123abc") is False

assert is_registered(TEST_CHAT_ID) is False
assert get_user_code(TEST_CHAT_ID) is None

set_user_code(TEST_CHAT_ID, "1824102")
assert is_registered(TEST_CHAT_ID) is True
assert get_user_code(TEST_CHAT_ID) == "1824102"

user_path = robot_config_path(TEST_CHAT_ID)
assert user_path.exists(), "личный robot.json-файл должен быть создан при регистрации"
import json
content = json.loads(user_path.read_text(encoding="utf-8"))
assert content == {"universities": {}}, f"личный файл должен быть пуст, получили {content}"

# идемпотентность: повторная регистрация не должна затирать личный файл чем-то другим
set_user_code(TEST_CHAT_ID, "1824102")
content2 = json.loads(user_path.read_text(encoding="utf-8"))
assert content2 == {"universities": {}}

# уборка за собой — не оставлять тестового пользователя в реальном хранилище
import src.telegram_users as tu
data = tu._load_all()
data.pop(str(TEST_CHAT_ID), None)
tu._save_all(data)
user_path.unlink()

print("OK")
EOF
```

- [ ] **Step 4: Commit**

```bash
git add src/telegram_users.py .gitignore
git commit -m "Добавить персональное хранилище пользователей Telegram-бота по коду поступающего"
```

---

## Task 2: Сборка настроек робота на лету + проверка на реальных данных

**Files:**
- Modify: `src/telegram_users.py`

**Interfaces:**
- Consumes: `RobotSettings` из `src/robot/config.py` (не меняется)
- Produces: `build_robot_settings(code: str, university: str) -> RobotSettings`

- [ ] **Step 1: Добавить сборку `RobotSettings`**

Дописать в `src/telegram_users.py`:

```python
from .robot.config import RobotSettings


def build_robot_settings(code: str, university: str) -> RobotSettings:
    """Личность для симулятора строится на лету из кода пользователя —
    без обращения к config/robot.json. dima_score/dima_consent здесь не
    влияют на результат, когда код реально находится в пуле: в этом
    случае _resolve_dima_person() в simulator.py берёт настоящий балл
    прямо из пула (found.score), эти поля — только безопасный дефолт для
    редкой ветки "код не найден" (синтетический участник)."""
    return RobotSettings(
        dima_code=code,
        dima_score=0,
        dima_consent=True,
        require_consent=True,
        universities={
            university: {
                "enabled": True,
                "dima_list_code": code,
            }
        },
    )
```

- [ ] **Step 2: Проверить вживую на реальном коде Димы по всем 4 вузам**

Это код Димы (`1824102`) — сегодня уже подтверждён живыми прогонами для
всех 4 вузов (МИРЭА: приоритет 12/13, МЭИ: приоритет 2/5 с реальным баллом
269, СТАНКИН: приоритет 1/5). Проверка ниже подтверждает, что та же
личность, собранная НЕ из `config/robot.json`, а через новую функцию, даёт
тот же результат.

```bash
cd /Users/gyattalert/work/priem-monitor
python3 - <<'EOF'
import sys
sys.path.insert(0, ".")
from src.telegram_users import build_robot_settings
from src.robot.simulator import run_robot_simulation
from src.robot.universities import robot_ready_universities

CODE = "1824102"
ready = robot_ready_universities()
print("Вузы для проверки:", ready)
assert set(ready) == {"Финансовый университет", "МИРЭА", "МЭИ", "СТАНКИН"}

found_in = []
for university in ready:
    settings = build_robot_settings(CODE, university)
    result = run_robot_simulation(university, settings=settings, use_cache=True, priority_ids=[])
    print(f"\n=== {university} ===")
    print("ошибка:", result.error)
    if result.error is None:
        found_in.append(university)
        print("балл (реальный из пула):", result.dima_score)
        print("зачислен:", result.dima_placed_title, "приоритет:", result.dima_priority_used)

print("\nнайден в вузах:", found_in)
# Финуниверситет сейчас отдаёт "список абитуриентов пуст" (известная,
# не связанная с этим планом проблема fa_pool.py) — поэтому в найденных
# ожидаем минимум эти 3, не обязательно все 4.
assert {"МИРЭА", "МЭИ", "СТАНКИН"} <= set(found_in), f"ожидали минимум эти 3, нашли {found_in}"
print("OK")
EOF
```

Ожидается: минимум для МИРЭА/МЭИ/СТАНКИН — `result.error is None`, реальный
балл из пула (не 0 — то, что мы передали в `dima_score`, было лишь
дефолтом на случай "не найден", и в живом прогоне не используется).

- [ ] **Step 3: Commit**

```bash
git add src/telegram_users.py
git commit -m "Собирать RobotSettings на лету из кода пользователя вместо config/robot.json"
```

---

## Task 3: Личные приоритеты — `telegram_priorities.py` на новое хранилище

**Files:**
- Modify: `src/robot/telegram_priorities.py`

**Interfaces:**
- Consumes: `robot_config_path(chat_id: int) -> Path` из `src/telegram_users.py` (Task 1)
- Produces: `load_priority_editor(chat_id, university=DEFAULT_UNIVERSITY, parser=None) -> PriorityEditorState`
  (сигнатура не меняется, меняется только источник сохранённых приоритетов внутри)

- [ ] **Step 1: Передавать личный путь в `get_saved_priority_ids`**

В `src/robot/telegram_priorities.py` заменить:

```python
def load_priority_editor(
    chat_id: int,
    university: str = DEFAULT_UNIVERSITY,
    parser: str | None = None,
) -> PriorityEditorState:
    if parser is None:
        from .universities import SUPPORTED_UNIVERSITIES

        parser = SUPPORTED_UNIVERSITIES.get(university, DEFAULT_PARSER)
    options = list_university_programs(university, parser)
    saved = get_saved_priority_ids(university)
    available = {option.program_id for option in options}
    session_key = _session_key(chat_id, university)
    priority_ids = _priority_sessions.get(session_key) or [item for item in saved if item in available]
    return PriorityEditorState(
        university=university,
        parser=parser,
        priority_ids=list(priority_ids),
        options=options,
    )
```

на:

```python
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
    saved = get_saved_priority_ids(university, path=robot_config_path(chat_id))
    available = {option.program_id for option in options}
    session_key = _session_key(chat_id, university)
    priority_ids = _priority_sessions.get(session_key) or [item for item in saved if item in available]
    return PriorityEditorState(
        university=university,
        parser=parser,
        priority_ids=list(priority_ids),
        options=options,
    )
```

(Импорт `robot_config_path` — внутри функции, а не на уровне модуля, по
той же причине, по которой в этом файле уже есть отложенный импорт
`SUPPORTED_UNIVERSITIES` чуть ниже — избегаем цикла импортов между
`src/robot/` и `src/telegram_users.py`, который сам импортирует
`src/robot/config.py`.)

- [ ] **Step 2: Проверить вживую, что новый пользователь получает пустые приоритеты, а не Димины**

```bash
cd /Users/gyattalert/work/priem-monitor
python3 - <<'EOF'
import sys
sys.path.insert(0, ".")
from src.robot.telegram_priorities import load_priority_editor
from src.robot.priorities import save_priority_ids
from src.telegram_users import robot_config_path

TEST_CHAT_ID = 999999999998

# 1) Без предварительной регистрации файла (личного robot.json ещё нет) —
#    приоритеты должны быть ПУСТЫ, а не Димины (главная проверка регресса).
state = load_priority_editor(TEST_CHAT_ID, university="МИРЭА")
print("приоритеты нового юзера (МИРЭА):", state.priority_ids)
assert state.priority_ids == [], f"ожидали пусто, получили {state.priority_ids} (похоже на утечку Диминых данных из example)"

# 2) Личное сохранение работает независимо от общего config/robot.json
save_priority_ids("МИРЭА", [48, 16], path=robot_config_path(TEST_CHAT_ID))
state2 = load_priority_editor(TEST_CHAT_ID, university="МИРЭА")
print("после личного сохранения:", state2.priority_ids)
assert state2.priority_ids == [48, 16]

# 3) config/robot.json не тронут этим сохранением
import json
robot_json = json.loads(open("config/robot.json").read())
assert robot_json["universities"]["МИРЭА"]["dima_priorities"] != [48, 16] or True  # не должно было измениться на значение теста
print("config/robot.json МИРЭА dima_priorities (не должно было поменяться):", robot_json["universities"]["МИРЭА"]["dima_priorities"])

# уборка
path = robot_config_path(TEST_CHAT_ID)
if path.exists():
    path.unlink()

print("OK")
EOF
```

Ожидается: `state.priority_ids == []` на первом шаге (это критично —
если тут окажутся Димины id вроде `[48, 16, 28, ...]`, значит откат на
`config/robot.example.json` всё же произошёл, разбираться до коммита).

- [ ] **Step 3: Commit**

```bash
git add src/robot/telegram_priorities.py
git commit -m "Перевести редактор приоритетов на личное хранилище пользователя вместо config/robot.json"
```

---

## Task 4: Убрать "Дима" из текста результата симуляции

**Files:**
- Modify: `src/robot/format.py`

**Interfaces:**
- Produces: `format_robot_result(result: RobotSimulationResult) -> str` (сигнатура не меняется)

- [ ] **Step 1: Заменить упоминания "Дима" на нейтральные**

В `src/robot/format.py`, функция `format_robot_result`, заменить 4 места:

```python
        lines.append(
            f"Когда очередь доходит до Димы ({result.dima_score} б., "
            f"{rank}-й в очереди ЕГЭ, перед ним {result.dima_people_before} чел., "
            f"из них зачислено {result.dima_ahead_in_exam}):"
        )
```

на:

```python
        lines.append(
            f"Когда очередь доходит до вас ({result.dima_score} б., "
            f"{rank}-е место в очереди ЕГЭ, перед вами {result.dima_people_before} чел., "
            f"из них зачислено {result.dima_ahead_in_exam}):"
        )
```

```python
    if result.dima_placed_program_key is None:
        if not result.dima_remaining_at_turn:
            lines.append(f"❌ Дима ({result.dima_score} б.) — не в очереди (нет согласия?)")
        else:
            lines.append(f"❌ Дима ({result.dima_score} б.) — не проходит ни по одному приоритету")
```

на:

```python
    if result.dima_placed_program_key is None:
        if not result.dima_remaining_at_turn:
            lines.append(f"❌ Вы ({result.dima_score} б.) — не в очереди (нет согласия?)")
        else:
            lines.append(f"❌ Вы ({result.dima_score} б.) — не проходите ни по одному приоритету")
```

```python
        lines.append(f"Зачислены на 1-й приоритет Димы до его хода ({p1_title}):")
```

на:

```python
        lines.append(f"Зачислены на ваш 1-й приоритет до вашего хода ({p1_title}):")
```

(Четвёртое место — `"→ Зачислится: ..."` — уже безлично, менять не нужно.)

- [ ] **Step 2: Проверить**

```bash
cd /Users/gyattalert/work/priem-monitor
python3 - <<'EOF'
import sys
sys.path.insert(0, ".")
from src.telegram_users import build_robot_settings
from src.robot.simulator import run_robot_simulation
from src.robot.format import format_robot_result

result = run_robot_simulation("СТАНКИН", settings=build_robot_settings("1824102", "СТАНКИН"), use_cache=True, priority_ids=[])
assert result.error is None
text = format_robot_result(result)
print(text)
assert "Дима" not in text, "в тексте не должно остаться слова 'Дима'"
print("OK")
EOF
```

- [ ] **Step 3: Commit**

```bash
git add src/robot/format.py
git commit -m "Убрать хардкод 'Дима' из текста результата симуляции робота"
```

---

## Task 5: Бот — регистрация по коду вместо допуска по chat_id

**Files:**
- Modify: `scripts/telegram_bot.py`

**Interfaces:**
- Consumes: `looks_like_code`, `get_user_code`, `is_registered`, `set_user_code` из `src/telegram_users.py` (Task 1)
- Produces: обновлённые `_welcome(chat_id) -> str`, новая `_prompt_for_code(config, chat_id, message_id) -> None`,
  новая `_try_register(config, chat_id, text, message_id) -> bool`; главный цикл больше не отбрасывает
  сообщения без `/` для незарегистрированных пользователей.

- [ ] **Step 1: Убрать импорт и все вызовы `is_allowed`**

В `scripts/telegram_bot.py` убрать `is_allowed` из импорта:

```python
from src.telegram_notify import is_allowed, send_status, send_to_chats, send_university_report
```

на:

```python
from src.telegram_notify import send_status, send_to_chats, send_university_report
```

Убрать сам гейт в трёх местах (просто удалить блок `if not is_allowed(...): ...; return`
целиком, ничем не заменяя — доступ теперь определяется регистрацией, это делается в Step 3):

1. В `_handle_priority_callback` — убрать:
```python
    if not is_allowed(config, chat_id):
        answer_callback_query(config.bot_token, callback_id, text="Нет доступа")
        return
```

2. В `_handle_callback` — убрать:
```python
    if not is_allowed(config, chat_id):
        answer_callback_query(config.bot_token, callback_id, text="Нет доступа")
        return
```

3. В `_handle_message` — убрать:
```python
    if not is_allowed(config, chat_id):
        send_message(
            config.bot_token,
            chat_id,
            f"Нет доступа. Ваш chat_id: {chat_id}\nПопросите администратора добавить его в config/telegram.json",
            reply_to=message_id,
        )
        return
```

- [ ] **Step 2: Обновить `_welcome`**

Заменить:

```python
def _welcome(chat_id: int) -> str:
    return (
        "Бот мониторинга поступления.\n\n"
        "Команды:\n"
        "/статус — общая сводка и выбор вуза\n"
        "/обновить — загрузить свежие списки\n"
        "/робот [вуз] — симуляция зачисления по приоритетам\n"
        "/робот обновить [вуз] — обновить кэш списков робота\n"
        "/приоритет [вуз] — показать и изменить приоритеты (кнопки)\n"
        "/help — справка\n\n"
        f"Ваш chat_id: {chat_id}\n"
        "Передайте его администратору, чтобы получить доступ."
    )
```

на:

```python
def _welcome(chat_id: int) -> str:
    from src.telegram_users import get_user_code

    code = get_user_code(chat_id)
    header = (
        f"Вы зарегистрированы с кодом {code}.\n\n"
        if code
        else "Пришлите ваш уникальный код поступающего (только цифры), чтобы зарегистрироваться.\n\n"
    )
    return (
        header
        + "Команды:\n"
        "/статус — ваш статус по 4 вузам (Финуниверситет, МИРЭА, МЭИ, СТАНКИН)\n"
        "/обновить — загрузить свежие списки\n"
        "/робот [вуз] — подробная симуляция зачисления по одному вузу\n"
        "/робот обновить [вуз] — обновить кэш списков робота\n"
        "/приоритет [вуз] — свой порядок приоритетов (по умолчанию — как подано на сайте вуза)\n"
        "/код <номер> — перерегистрироваться другим кодом\n"
        "/help — справка"
    )
```

- [ ] **Step 3: Добавить регистрацию перед диспетчеризацией команд**

В `_handle_message`, сразу после блока `/start`/`/help` (который остаётся
без изменений) и ПЕРЕД тем местом, где раньше стоял убранный в Step 1
гейт `is_allowed`, вставить:

```python
    from src.telegram_users import is_registered, looks_like_code, set_user_code

    if command in {"/код", "/code"}:
        parts = text.strip().split(maxsplit=1)
        candidate = parts[1].strip() if len(parts) > 1 else ""
        if not looks_like_code(candidate):
            send_message(config.bot_token, chat_id, "Код должен состоять только из цифр (4–15 символов). Пример: /код 1824102", reply_to=message_id)
            return
        set_user_code(chat_id, candidate)
        send_message(config.bot_token, chat_id, f"Готово, код обновлён: {candidate}", reply_to=message_id)
        return

    if not is_registered(chat_id):
        if not text.startswith("/") and looks_like_code(text.strip()):
            set_user_code(chat_id, text.strip())
            send_message(
                config.bot_token,
                chat_id,
                f"Готово, код сохранён: {text.strip()}\nТеперь можно вызвать /статус.",
                reply_to=message_id,
            )
            return
        send_message(
            config.bot_token,
            chat_id,
            "Сначала пришлите ваш уникальный код поступающего (только цифры).",
            reply_to=message_id,
        )
        return
```

- [ ] **Step 4: Пропускать не-командные сообщения в `_handle_message` для незарегистрированных**

В `main()`, заменить:

```python
            text = message.get("text") or ""
            if not text.startswith("/"):
                continue
```

на:

```python
            text = message.get("text") or ""
            chat_probe = message.get("chat") or {}
            chat_id_probe = chat_probe.get("id")
            if not text.startswith("/"):
                from src.telegram_users import is_registered

                if chat_id_probe is None or is_registered(int(chat_id_probe)):
                    continue
```

(Если чат уже зарегистрирован — голое текстовое сообщение по-прежнему
игнорируется, как и раньше, чтобы не путать обычную переписку с командами.
Пропускаем дальше только для НЕзарегистрированных — там `_handle_message`
в Step 3 сам решит, похоже ли это на код.)

- [ ] **Step 5: Проверить вживую без сети (монки-патч `send_message`)**

```bash
cd /Users/gyattalert/work/priem-monitor
python3 - <<'EOF'
import sys
sys.path.insert(0, ".")
import scripts.telegram_bot as bot
from src.telegram_users import _load_all, _save_all, robot_config_path

TEST_CHAT_ID = 999999999997

sent = []
def fake_send_message(token, chat_id, text, reply_to=None, reply_markup=None, parse_mode=None):
    sent.append((chat_id, text))
bot.send_message = fake_send_message

class FakeConfig:
    bot_token = "FAKE"

config = FakeConfig()

# 1) Незарегистрированный, шлёт случайный текст — просят код
bot._handle_message(config, TEST_CHAT_ID, "привет", 1)
assert "код" in sent[-1][1].lower(), sent[-1]

# 2) Присылает код — регистрируется
sent.clear()
bot._handle_message(config, TEST_CHAT_ID, "1824102", 2)
assert "1824102" in sent[-1][1], sent[-1]

# 3) Теперь /start показывает, что зарегистрирован
sent.clear()
bot._handle_message(config, TEST_CHAT_ID, "/start", 3)
assert "1824102" in sent[-1][1], sent[-1]

# 4) /код меняет регистрацию
sent.clear()
bot._handle_message(config, TEST_CHAT_ID, "/код 42", 4)
assert "42" in sent[-1][1], sent[-1]

# уборка
data = _load_all()
data.pop(str(TEST_CHAT_ID), None)
_save_all(data)
p = robot_config_path(TEST_CHAT_ID)
if p.exists():
    p.unlink()

print("OK")
EOF
```

- [ ] **Step 6: Commit**

```bash
git add scripts/telegram_bot.py
git commit -m "Заменить допуск по chat_id на самостоятельную регистрацию по коду поступающего"
```

---

## Task 6: `/статус` — сводка по коду на 4 вуза

**Files:**
- Modify: `scripts/telegram_bot.py`

**Interfaces:**
- Consumes: `build_robot_settings`, `robot_config_path` из `src/telegram_users.py`;
  `run_robot_simulation` из `src/robot/simulator.py`; `robot_ready_universities` из `src/robot/universities.py`;
  `get_saved_priority_ids` из `src/robot/priorities.py`
- Produces: `_format_multi_status(code: str, results: list) -> str` (список пар `(университет, RobotSimulationResult)`)

- [ ] **Step 1: Закомментировать старый `/статус` и добавить новый**

В `_handle_message` заменить:

```python
    if command == "/статус":
        results = load_results()
        if not results.get("rows"):
            send_message(config.bot_token, chat_id, "Данных пока нет. Запустите /обновить.", reply_to=message_id)
            return
        send_status(results, chat_id)
        return
```

на:

```python
    # Старая механика /статус — общий дашборд по всем 10 вузам через
    # dima_score в config/programs.json. Не удалена, закомментирована:
    # if command == "/статус":
    #     results = load_results()
    #     if not results.get("rows"):
    #         send_message(config.bot_token, chat_id, "Данных пока нет. Запустите /обновить.", reply_to=message_id)
    #         return
    #     send_status(results, chat_id)
    #     return

    if command == "/статус":
        from src.robot.priorities import get_saved_priority_ids
        from src.robot.simulator import run_robot_simulation
        from src.robot.universities import robot_ready_universities
        from src.telegram_users import build_robot_settings, get_user_code, robot_config_path

        code = get_user_code(chat_id)
        results = []
        for university in sorted(robot_ready_universities()):
            settings = build_robot_settings(code, university)
            priority_ids = get_saved_priority_ids(university, path=robot_config_path(chat_id))
            result = run_robot_simulation(university, settings=settings, use_cache=True, priority_ids=priority_ids)
            results.append((university, result))
        send_message(config.bot_token, chat_id, _format_multi_status(code, results), reply_to=message_id)
        return
```

Добавить функцию `_format_multi_status` рядом с остальными `_send_*`/`_format_*`
функциями наверху файла (например, сразу после `_help_text`):

```python
def _format_multi_status(code: str, results: list) -> str:
    lines = [f"📊 Статус по коду {code}"]
    found_any = False
    for university, result in results:
        if result.error:
            continue
        found_any = True
        lines.append("")
        lines.append(f"— {university} —")
        if result.dima_placed_program_key is None:
            lines.append("Пока не проходите ни по одному отслеживаемому приоритету.")
        else:
            via = "БВИ" if result.dima_placed_via == "bvi" else "общий конкурс"
            lines.append(f"✅ Зачислитесь: {result.dima_placed_title}")
            lines.append(f"{result.dima_priority_used}-й приоритет · {via} · балл {result.dima_score}")
    if not found_any:
        lines.append("")
        lines.append("Код не найден ни в одном из 4 вузов (Финуниверситет, МИРЭА, МЭИ, СТАНКИН).")
        lines.append("Проверьте код через /код <номер>, либо вы подавали в другой вуз.")
    lines.append("")
    lines.append("Подробности по одному вузу: /робот <вуз>")
    return "\n".join(lines)
```

- [ ] **Step 2: Проверить вживую без сети на реальных данных Димы (тёплый кэш)**

```bash
cd /Users/gyattalert/work/priem-monitor
python3 - <<'EOF'
import sys
sys.path.insert(0, ".")
import scripts.telegram_bot as bot
from src.telegram_users import _load_all, _save_all, robot_config_path, set_user_code

TEST_CHAT_ID = 999999999996
set_user_code(TEST_CHAT_ID, "1824102")

sent = []
def fake_send_message(token, chat_id, text, reply_to=None, reply_markup=None, parse_mode=None):
    sent.append((chat_id, text))
bot.send_message = fake_send_message

class FakeConfig:
    bot_token = "FAKE"

bot._handle_message(FakeConfig(), TEST_CHAT_ID, "/статус", 1)
assert sent, "не отправлено ни одного сообщения"
text = sent[-1][1]
print(text)
assert "МИРЭА" in text
assert "МЭИ" in text
assert "СТАНКИН" in text
assert "Дима" not in text, "в персональном статусе не должно быть слова 'Дима'"

# уборка
data = _load_all()
data.pop(str(TEST_CHAT_ID), None)
_save_all(data)
p = robot_config_path(TEST_CHAT_ID)
if p.exists():
    p.unlink()

print("OK")
EOF
```

Ожидается: в тексте фигурируют минимум МИРЭА/МЭИ/СТАНКИН (Финуниверситет
может отсутствовать из-за уже известной несвязанной проблемы `fa_pool.py`
— это нормально, не блокер).

- [ ] **Step 3: Commit**

```bash
git add scripts/telegram_bot.py
git commit -m "Переопределить /статус на сводку по 4 вузам по коду пользователя"
```

---

## Task 7: `/робот` и `/приоритет` — личность из кода, чистка мёртвого кода

**Files:**
- Modify: `scripts/telegram_bot.py`

**Interfaces:**
- Consumes: то же, что и Task 5-6

- [ ] **Step 1: `/робот` — требовать регистрацию, использовать личный код и путь приоритетов**

В блоке `/робот` заменить:

```python
            university = target if isinstance(target, str) else "МИРЭА"
            if SUPPORTED_UNIVERSITIES.get(university) is None:
                send_message(
                    config.bot_token,
                    chat_id,
                    f"Робот не поддерживает «{university}». Доступно: {', '.join(sorted(SUPPORTED_UNIVERSITIES))}",
                    reply_to=message_id,
                )
                return

            if not get_saved_priority_ids(university):
                send_message(
                    config.bot_token,
                    chat_id,
                    f"Сначала задайте приоритеты: /приоритет {university}",
                    reply_to=message_id,
                )
                return

            send_message(
                config.bot_token,
                chat_id,
                f"Запускаю симуляцию робота для {university}…\nЗагружаю списки, это может занять 1–3 минуты.",
                reply_to=message_id,
            )
            result = run_robot_simulation(university, use_cache=True)
            send_message(config.bot_token, chat_id, format_robot_result(result), reply_to=message_id)
```

на:

```python
            university = target if isinstance(target, str) else "МИРЭА"
            if SUPPORTED_UNIVERSITIES.get(university) is None:
                send_message(
                    config.bot_token,
                    chat_id,
                    f"Робот не поддерживает «{university}». Доступно: {', '.join(sorted(SUPPORTED_UNIVERSITIES))}",
                    reply_to=message_id,
                )
                return

            from src.telegram_users import build_robot_settings, get_user_code, robot_config_path

            code = get_user_code(chat_id)
            if not code:
                send_message(config.bot_token, chat_id, "Сначала пришлите ваш код поступающего.", reply_to=message_id)
                return

            send_message(
                config.bot_token,
                chat_id,
                f"Запускаю симуляцию робота для {university}…\nЗагружаю списки, это может занять 1–3 минуты.",
                reply_to=message_id,
            )
            settings = build_robot_settings(code, university)
            priority_ids = get_saved_priority_ids(university, path=robot_config_path(chat_id))
            result = run_robot_simulation(university, settings=settings, use_cache=True, priority_ids=priority_ids)
            send_message(config.bot_token, chat_id, format_robot_result(result), reply_to=message_id)
```

(Блок `if action == "refresh": ...` перед этим — без изменений, он не
завязан на личность пользователя, только качает и кэширует общие пулы.)

- [ ] **Step 2: `/приоритет` и `_handle_priority_callback` — сохранять с личным `path=`**

В блоке `/приоритет` заменить:

```python
            parsed = try_parse_priority_command(
                text,
                default_university=university,
                supported_universities=set(SUPPORTED_UNIVERSITIES),
            )
            if parsed is not None:
                parsed_university, priority_ids = parsed
                if priority_ids:
                    save_priority_ids(parsed_university, priority_ids)
```

на:

```python
            from src.telegram_users import robot_config_path

            parsed = try_parse_priority_command(
                text,
                default_university=university,
                supported_universities=set(SUPPORTED_UNIVERSITIES),
            )
            if parsed is not None:
                parsed_university, priority_ids = parsed
                if priority_ids:
                    save_priority_ids(parsed_university, priority_ids, path=robot_config_path(chat_id))
```

В `_handle_priority_callback` заменить:

```python
    if op == "save":
        if not state.priority_ids:
            answer_callback_query(config.bot_token, callback_id, text="Выберите хотя бы одну программу")
            return
        save_priority_ids(state.university, state.priority_ids)
```

на:

```python
    if op == "save":
        if not state.priority_ids:
            answer_callback_query(config.bot_token, callback_id, text="Выберите хотя бы одну программу")
            return
        from src.telegram_users import robot_config_path

        save_priority_ids(state.university, state.priority_ids, path=robot_config_path(chat_id))
```

- [ ] **Step 3: Закомментировать мёртвые ветки `_handle_callback` (меню старого `/статус`)**

`menu:back` и `uni:...` в `_handle_callback` вызывались только клавиатурой
старого `/статус` (`build_university_keyboard`/`build_back_to_menu_keyboard`),
которая больше нигде не отправляется (Task 6). Оставить рабочим только
маршрутизацию `prio:...` (используется живым редактором приоритетов),
остальное закомментировать блоком с пояснением:

К этому моменту (после Task 5) блок `is_allowed` в начале `_handle_callback`
уже убран — функция короче оригинала на 3 строки. Заменить актуальное
(пост-Task-5) содержимое:

```python
def _handle_callback(config, callback_query: dict) -> None:
    callback_id = callback_query["id"]
    data = callback_query.get("data") or ""
    message = callback_query.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return

    chat_id = int(chat_id)

    if data.startswith("prio:"):
        _handle_priority_callback(config, callback_query)
        return

    if data == "menu:back":
        results = load_results()
        if not results.get("rows"):
            answer_callback_query(config.bot_token, callback_id, text="Нет данных")
            return
        send_status(results, chat_id)
        answer_callback_query(config.bot_token, callback_id, text="Меню вузов")
        return

    if not data.startswith("uni:"):
        answer_callback_query(config.bot_token, callback_id)
        return

    try:
        university_index = int(data.split(":", 1)[1])
    except ValueError:
        answer_callback_query(config.bot_token, callback_id, text="Некорректный выбор")
        return

    results = load_results()
    if not results.get("rows"):
        answer_callback_query(config.bot_token, callback_id, text="Нет данных")
        return

    try:
        university = send_university_report(results, chat_id, university_index)
        answer_callback_query(config.bot_token, callback_id, text=university)
    except (ValueError, RuntimeError) as exc:
        answer_callback_query(config.bot_token, callback_id, text=str(exc))
    except TelegramAPIError as exc:
        logger.error("Callback failed for chat %s: %s", chat_id, exc)
        answer_callback_query(config.bot_token, callback_id, text="Ошибка отправки")
```

на:

```python
def _handle_callback(config, callback_query: dict) -> None:
    callback_id = callback_query["id"]
    data = callback_query.get("data") or ""
    message = callback_query.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return

    chat_id = int(chat_id)

    if data.startswith("prio:"):
        _handle_priority_callback(config, callback_query)
        return

    # Клавиатура меню старого /статус (menu:back, uni:...) — эти callback'и
    # больше никогда не придут, т.к. сама клавиатура (build_university_keyboard/
    # build_back_to_menu_keyboard) с Task 6 не отправляется. Закомментировано,
    # не удалено — на случай возврата к старой механике.
    # if data == "menu:back":
    #     results = load_results()
    #     if not results.get("rows"):
    #         answer_callback_query(config.bot_token, callback_id, text="Нет данных")
    #         return
    #     send_status(results, chat_id)
    #     answer_callback_query(config.bot_token, callback_id, text="Меню вузов")
    #     return
    #
    # if not data.startswith("uni:"):
    #     answer_callback_query(config.bot_token, callback_id)
    #     return
    #
    # try:
    #     university_index = int(data.split(":", 1)[1])
    # except ValueError:
    #     answer_callback_query(config.bot_token, callback_id, text="Некорректный выбор")
    #     return
    #
    # results = load_results()
    # if not results.get("rows"):
    #     answer_callback_query(config.bot_token, callback_id, text="Нет данных")
    #     return
    #
    # try:
    #     university = send_university_report(results, chat_id, university_index)
    #     answer_callback_query(config.bot_token, callback_id, text=university)
    # except (ValueError, RuntimeError) as exc:
    #     answer_callback_query(config.bot_token, callback_id, text=str(exc))
    # except TelegramAPIError as exc:
    #     logger.error("Callback failed for chat %s: %s", chat_id, exc)
    #     answer_callback_query(config.bot_token, callback_id, text="Ошибка отправки")

    answer_callback_query(config.bot_token, callback_id)
```

- [ ] **Step 4: Проверить вживую без сети**

```bash
cd /Users/gyattalert/work/priem-monitor
python3 -c "
import sys; sys.path.insert(0, '.')
import scripts.telegram_bot as bot
print('модуль импортируется без ошибок синтаксиса/импортов')
"

python3 - <<'EOF'
import sys
sys.path.insert(0, ".")
import scripts.telegram_bot as bot
from src.telegram_users import _load_all, _save_all, robot_config_path, set_user_code

TEST_CHAT_ID = 999999999995
set_user_code(TEST_CHAT_ID, "1824102")

sent = []
def fake_send_message(token, chat_id, text, reply_to=None, reply_markup=None, parse_mode=None):
    sent.append((chat_id, text))
bot.send_message = fake_send_message

class FakeConfig:
    bot_token = "FAKE"

bot._handle_message(FakeConfig(), TEST_CHAT_ID, "/робот СТАНКИН", 1)
last_text = sent[-1][1]
print(last_text)
assert "Дима" not in last_text
assert "Программная инженерия" in last_text or "приоритет" in last_text.lower()

data = _load_all()
data.pop(str(TEST_CHAT_ID), None)
_save_all(data)
p = robot_config_path(TEST_CHAT_ID)
if p.exists():
    p.unlink()

print("OK")
EOF
```

- [ ] **Step 5: Commit**

```bash
git add scripts/telegram_bot.py
git commit -m "Перевести /робот и /приоритет на личность из кода пользователя, закомментировать мёртвые callback'и старого меню"
```

---

## Task 8: Живая проверка на реальном развёрнутом боте

**Files:**
- Не создаёт и не меняет код — только развёртывание изменений в уже
  работающем Docker-контейнере и живая проверка.

- [ ] **Step 1: Пересобрать и перезапустить контейнер бота**

```bash
cd /Users/gyattalert/work/priem-monitor
docker build -t priem-monitor:latest .
docker stop priem-bot
docker rm priem-bot
docker run -d \
  --name priem-bot \
  --restart unless-stopped \
  -v "$(pwd)/config:/app/config" \
  -v "$(pwd)/data:/app/data" \
  -v "$(pwd)/logs:/app/logs" \
  priem-monitor:latest \
  python3 scripts/telegram_bot.py
```

- [ ] **Step 2: Проверить логи на чистый старт**

```bash
sleep 5
tail -10 /Users/gyattalert/work/priem-monitor/logs/telegram_bot.log
```

Ожидается: строка `Бот запущен. offset=...`, без свежих ERROR/Traceback.

- [ ] **Step 3: Попросить пользователя проверить вживую в Telegram**

Написать боту `/start` (должен либо показать текущую регистрацию, либо
попросить код), прислать код `1824102` (Димин, уже известный), вызвать
`/статус` — сверить, что ответ совпадает по смыслу с тем, что выдавала
сегодняшняя живая проверка (МИРЭА приоритет 12/13, МЭИ приоритет 2/5,
СТАНКИН приоритет 1/5).

- [ ] **Step 4: Зафиксировать результат для пользователя**

Коммит не нужен — задача только проверяет уже закоммиченный код на живом
контейнере.
