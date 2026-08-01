# Реальность бюджетных мест в робот-пулах — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Робот перестаёт показывать выдуманное число мест `30` для отслеживаемых направлений (вместо этого — честное «нет данных»), а config ФА приводится к реальным местам общего конкурса по официальному КЦП.

**Architecture:** Часть A делает `budget_places` nullable (`int | None`) и проводит `None` через симулятор и форматтер как «места: нет данных» без ✅/❌, а константу `DEFAULT_BUDGET_PLACES = 30` оставляет только как явную аппроксимацию для непрофильных untracked-программ каскада. Часть B — точечная правка двух значений в `config/programs.json`.

**Tech Stack:** Python 3.12, dataclasses. Без новых зависимостей.

## Global Constraints

- Спека: `docs/superpowers/specs/2026-08-02-budget-places-reality-design.md`. Задачи beads: `priem-monitor-q1d` (убрать `30`) + `priem-monitor-v0j` (правка КЦП).
- Проектный принцип: данные, которые не удалось получить, — ВИДИМАЯ метка «нет данных», НИКОГДА не выдуманное/усреднённое число.
- Охват гарантии «реальное число или нет данных» — только **отслеживаемые** направления (в `config/programs.json`). Непрофильные untracked-программы каскада сохраняют аппроксимацию мест.
- Оставляем как валидные источники: config-значения, `FA_PLACES_OVERRIDES`, STANKIN-сумму config-квот смёрженных пар. `mirea_pool.py` не трогаем (нет `30`, живой API).
- Места общего конкурса ФА = КЦП − особая − отдельная − целевая квота.
- В проекте нет pytest для робот-пулов — проверка вживую, как во всех планах репозитория.
- Коммиты — короткое повелительное наклонение на русском, без префиксов, без `Co-Authored-By`.

---

### Task 1: `budget_places` nullable + «нет данных» (Часть A)

**Files:**
- Modify: `src/robot/models.py` (`RobotProgram`, `ProgramState`, `DimaPrioritySnapshot`)
- Modify: `src/robot/simulator.py` (`_try_place`, `_dima_remaining_snapshot`, `_passing_score_for_program`, `_simulate_two_phase`)
- Modify: `src/robot/format.py` (`format_robot_result` — блок снимка приоритетов)
- Modify: `src/robot/fa_pool.py`, `src/robot/mpei_pool.py`, `src/robot/stankin_pool.py` (константа + tracked-ветка)

**Interfaces:**
- Produces: `RobotProgram.budget_places: int | None`, `ProgramState.budget_places: int | None`, `ProgramState.remaining: int | None`, `DimaPrioritySnapshot.budget_places: int | None`, `DimaPrioritySnapshot.remaining_at_turn: int | None`. Используется только внутри `src/robot/`.

- [ ] **Step 1: Сделать поля мест nullable в моделях**

В `src/robot/models.py` изменить три датакласса.

`RobotProgram` (строки 38-43):
```python
@dataclass
class RobotProgram:
    key: str
    title: str
    budget_places: int | None
    tracked_id: int | None = None
```

`ProgramState` (строки 46-55):
```python
@dataclass
class ProgramState:
    program_key: str
    title: str
    budget_places: int | None
    remaining: int | None
    tracked_id: int | None = None
    bvi_enrolled: int = 0
    exam_enrolled: int = 0
    enrolled: list[str] = field(default_factory=list)
```

`DimaPrioritySnapshot` (строки 77-87) — включая `can_enter`, которое теперь безопасно к `None`:
```python
@dataclass
class DimaPrioritySnapshot:
    priority: int
    program_key: str
    title: str
    budget_places: int | None
    remaining_at_turn: int | None

    @property
    def can_enter(self) -> bool:
        return self.remaining_at_turn is not None and self.remaining_at_turn > 0
```

- [ ] **Step 2: Обработать `None` в симуляторе**

В `src/robot/simulator.py`:

`_try_place` (строки 161-172) — программа с неизвестными местами (`remaining is None`) не может принимать зачисления, участник каскадирует дальше:
```python
    for program_key in person.ordered_program_keys(phase=phase):
        state = states.get(program_key)
        if state is None or state.remaining is None or state.remaining <= 0:
            continue
        state.remaining -= 1
```
(остальное тело `_try_place` без изменений)

`_passing_score_for_program` (строки 208-212) — при неизвестных местах проходной не считаем:
```python
def _passing_score_for_program(state: ProgramState, people_by_code: dict[str, RobotPerson]) -> int | None:
    if state.remaining is None or state.remaining > 0:
        return None
    scores = [people_by_code[code].score for code in state.enrolled if code in people_by_code and people_by_code[code].score > 0]
    return min(scores) if scores else None
```

`_simulate_two_phase` — построение `states` (строки 265-274) должно оставлять `remaining=None` для программ без данных:
```python
    states = {
        program.key: ProgramState(
            program_key=program.key,
            title=program.title,
            budget_places=program.budget_places,
            remaining=program.budget_places,
            tracked_id=program.tracked_id,
        )
        for program in programs
    }
```
(код не меняется — `remaining=program.budget_places` уже пробрасывает `None`; этот шаг только подтверждает, что менять здесь ничего не нужно.)

`_dima_remaining_snapshot` (строки 189-197) — снимок пробрасывает `budget_places`/`remaining` как есть (могут быть `None`), менять не нужно; подтвердить, что `DimaPrioritySnapshot(...)` принимает `None` (после Step 1 — принимает).

- [ ] **Step 3: Показать «нет данных» в форматтере**

В `src/robot/format.py`, блок снимка приоритетов (строки 96-103), заменить:
```python
        for item in result.dima_remaining_at_turn:
            status = "✅" if item.can_enter else "❌"
            taken = item.budget_places - item.remaining_at_turn
            lines.append(
                f"  {item.priority}. {_short_title(item.title)}: "
                f"осталось {item.remaining_at_turn}/{item.budget_places} "
                f"(занято {taken}) {status}"
            )
```
на:
```python
        for item in result.dima_remaining_at_turn:
            if item.budget_places is None or item.remaining_at_turn is None:
                lines.append(f"  {item.priority}. {_short_title(item.title)}: места: нет данных")
                continue
            status = "✅" if item.can_enter else "❌"
            taken = item.budget_places - item.remaining_at_turn
            lines.append(
                f"  {item.priority}. {_short_title(item.title)}: "
                f"осталось {item.remaining_at_turn}/{item.budget_places} "
                f"(занято {taken}) {status}"
            )
```

- [ ] **Step 4: Убрать `30` из tracked-ветки пулов, задокументировать для untracked**

Цель: для отслеживаемых направлений выдуманная константа недостижима — при отсутствии реального значения ставится `None`; для непрофильных untracked-программ каскада остаётся явная аппроксимация.

`src/robot/mpei_pool.py`, строка 104 — переименовать константу:
```python
UNTRACKED_FALLBACK_PLACES = 30  # аппроксимация мест ТОЛЬКО для непрофильных untracked-программ каскада (вне охвата гарантии реальных мест)
```
и строки 286-288:
```python
            places = kcp_places.get(title)
            if places is None:
                places = (tracked_program.budget_places or None) if tracked_program else UNTRACKED_FALLBACK_PLACES
```
(`tracked_program.budget_places or None` → отслеживаемое направление с config-значением 0/пустым даёт `None` («нет данных»), а не подставляет число.)

`src/robot/stankin_pool.py`, строка 194 — переименовать константу:
```python
UNTRACKED_FALLBACK_PLACES = 30  # аппроксимация мест ТОЛЬКО для непрофильных untracked-программ каскада (вне охвата гарантии реальных мест)
```
и строка 321 (untracked-ветка `else`):
```python
                else:
                    places = UNTRACKED_FALLBACK_PLACES
```
(tracked-ветку STANKINa — сумму config-квот смёрженных пар — НЕ трогать, оставить как есть по спеке.)

`src/robot/fa_pool.py`, строка 35 — переименовать константу:
```python
UNTRACKED_FALLBACK_PLACES = 30  # аппроксимация мест ТОЛЬКО для непрофильных untracked-программ каталога ФА (вне охвата гарантии реальных мест)
```
и строка 255:
```python
                    budget_places=places.get(title, UNTRACKED_FALLBACK_PLACES),
```
(отслеживаемые титулы ФА всегда есть в `_places_map()` из config, поэтому untracked-фоллбэк их не касается — переименование только фиксирует роль константы.)

- [ ] **Step 5: Проверить вживую — обычный прогон не сломался + путь «нет данных» работает**

Обычный прогон (числа мест должны остаться как были — None для отслеживаемых сегодня не срабатывает):
```bash
cd /Users/gyattalert/work/priem-monitor
python3 -c "
from src.robot.simulator import run_robot_simulation
from src.robot.config import load_robot_config
settings = load_robot_config()
for uni in ('МИРЭА', 'МЭИ', 'СТАНКИН', 'Финансовый университет'):
    res = run_robot_simulation(uni, settings=settings, use_cache=True, priority_ids=None)
    print(uni, '| error:', res.error, '| directions:', res.directions_total if not res.error else '-')
"
```
Ожидается: у вузов с тёплым кэшем `error=None` и ненулевое число направлений; ни одной трассировки/`TypeError` из-за `None`. (Если кэш холодный и вуз даёт сетевую ошибку — это не регресс данного таска, но `TypeError`/`unsupported operand ... NoneType` быть не должно.)

Синтетическая проверка пути «нет данных» (изолированно, без сети) — программа с `budget_places=None` не роняет симулятор и печатается как «нет данных»:
```bash
cd /Users/gyattalert/work/priem-monitor
python3 -c "
from src.robot.models import RobotProgram, RobotPerson, ProgramChoice
from src.robot.simulator import _simulate_two_phase
from src.robot.format import format_robot_result

programs = [RobotProgram(key='X', title='Тест-направление', budget_places=None, tracked_id=1)]
dima = RobotPerson(code='DIMA', score=250, consent=True, choices=[ProgramChoice(program_key='X', priority=1)])
res = _simulate_two_phase('ТестВуз', programs, [dima], dima, dima_in_pool=False, require_consent=True, from_cache=False)
out = format_robot_result(res)
print(out)
assert 'нет данных' in out, 'ожидалась метка «нет данных»'
print('OK: None-путь не падает и печатает «нет данных»')
"
```
Ожидается: вывод содержит «места: нет данных», финальная строка `OK: ...`, без исключений.

- [ ] **Step 6: Commit**

```bash
cd /Users/gyattalert/work/priem-monitor
git add src/robot/models.py src/robot/simulator.py src/robot/format.py src/robot/fa_pool.py src/robot/mpei_pool.py src/robot/stankin_pool.py
git commit -m "Сделать budget_places робота nullable — показывать «нет данных» вместо выдуманного 30"
```

---

### Task 2: Правка config ФА до реальных мест общего конкурса (Часть B)

**Files:**
- Modify: `config/programs.json` (программы id 54 и id 78)

**Interfaces:**
- Consumes: не зависит от Task 1 (правка данных); может выполняться независимо.

- [ ] **Step 1: Проверить текущие значения**

```bash
cd /Users/gyattalert/work/priem-monitor
python3 -c "
import json
data = json.load(open('config/programs.json'))
for it in data:
    if it['id'] in (54, 78):
        print(it['id'], it.get('budget_places'), '|', it.get('program','')[:50])
"
```
Ожидается: `54 89 ...` и `78 25 ...` (значения до правки).

- [ ] **Step 2: Исправить два значения**

В `config/programs.json` у записи с `"id": 54` заменить `"budget_places": 89` на `"budget_places": 87`, у записи с `"id": 78` заменить `"budget_places": 25` на `"budget_places": 62`.

Основание (официальный КЦП ФА 2026/2027, очная форма, Москва; места общего конкурса = КЦП − отдельная − особая − целевая):
- id 54 «Прикладное машинное обучение»: 113 − 12 − 12 − 2 = **87**.
- id 78 «Прикладные информационные системы в экономике и финансах»: 79 − 8 − 8 − 1 = **62**.
- id 13 и id 42 уже верны (19), НЕ трогать.

Правку сделать точечно (найти объект по `"id": 54` / `"id": 78` и заменить только поле `budget_places`), не переформатируя остальной JSON. Проверить, что файл остаётся валидным JSON:
```bash
cd /Users/gyattalert/work/priem-monitor
python3 -c "import json; json.load(open('config/programs.json')); print('JSON валиден')"
```

- [ ] **Step 3: Проверить результат правки**

```bash
cd /Users/gyattalert/work/priem-monitor
python3 -c "
import json
data = json.load(open('config/programs.json'))
vals = {it['id']: it.get('budget_places') for it in data if it['id'] in (13, 42, 54, 78)}
print(vals)
assert vals[54] == 87 and vals[78] == 62 and vals[13] == 19 and vals[42] == 19, vals
print('OK: id54=87, id78=62, id13/42=19')
"
```
Ожидается: `{13: 19, 42: 19, 54: 87, 78: 62}` и строка `OK: ...`.

- [ ] **Step 4: Commit**

```bash
cd /Users/gyattalert/work/priem-monitor
git add config/programs.json
git commit -m "Исправить бюджетные места ФА до реальных мест общего конкурса по официальному КЦП (id54 89→87, id78 25→62)"
```

- [ ] **Step 5: Закрыть задачи beads**

```bash
~/.local/bin/bd close priem-monitor-q1d --reason "Убрана выдуманная константа 30 из tracked-ветки пулов, budget_places nullable, «нет данных» вместо заглушки"
~/.local/bin/bd close priem-monitor-v0j --reason "Config ФА сверен с официальным КЦП и исправлен (id54 89→87, id78 25→62); методология места общего конкурса = КЦП−особая−отдельная−целевая"
```

## Self-Review

- **Spec coverage:** nullable budget_places — Task 1 Step 1; убрать `30` из tracked, оставить аппроксимацию для untracked — Task 1 Step 4; симулятор пропускает `None` — Step 2; «нет данных» в выводе без ✅/❌ — Step 3; STANKIN-сумма и `mirea_pool.py` не тронуты — подтверждено (Step 4 не трогает tracked-ветку STANKINa, mirea вне списка файлов); config id54→87, id78→62, id13/42 без изменений — Task 2 Step 2; сверка/методология КЦП−все квоты — Task 2 Step 2 основание. Всё покрыто.
- **Placeholder scan:** плейсхолдеров/TODO нет; весь код — реальный.
- **Type consistency:** `budget_places: int | None` и `remaining: int | None` согласованы между `RobotProgram`/`ProgramState`/`DimaPrioritySnapshot` (Step 1) и их использованием в `simulator.py` (Step 2) и `format.py` (Step 3); константа единообразно переименована в `UNTRACKED_FALLBACK_PLACES` во всех трёх пулах (Step 4).
