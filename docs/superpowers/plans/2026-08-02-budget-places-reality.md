# Реальность бюджетных мест в робот-пулах — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Робот показывает реальные места **общего конкурса** по всем отслеживаемым направлениям 4 вузов (КЦП минус все квоты), а не полный КЦП/выдуманное `30`; при отсутствии данных — честное «нет данных».

**Architecture:** Часть A делает `budget_places` nullable и проводит `None` через симулятор/форматтер как «места: нет данных». Часть B приводит места к общему конкурсу: ФА и МЭИ — правкой config; МЭИ дополнительно — логикой пула (вычесть квоты из kcp-таблицы); СТАНКИН — override-мапой реальными основными местами (смёрженные пары); МИРЭА уже верен.

**Tech Stack:** Python 3.12, dataclasses, requests, beautifulsoup4. Без новых зависимостей.

## Global Constraints

- Спека: `docs/superpowers/specs/2026-08-02-budget-places-reality-design.md`. Задачи beads: `priem-monitor-q1d` + `priem-monitor-v0j`.
- Места общего конкурса = КЦП − особая − отдельная − целевая квота (пользователь идёт чистым ЕГЭ-конкурсом без БВИ/квот).
- Данные, которые не удалось получить, — ВИДИМАЯ метка «нет данных», НИКОГДА не выдуманное число.
- `mirea_pool.py` не трогаем (верно). Парсеры конкурсных списков и дашборд/бот вне робота не трогаем.
- Официальные числа (проверены): ФА id54=87, id78=62; МЭИ id7=13, id22=36, id23=23, id35=38, id45=130; СТАНКИН основные по группам: 09.03.04=47, 09.03.03.01=40, 09.03.03.02=57, 09.03.01.01=19, 09.03.02.01=40.
- В проекте нет pytest для робот-пулов — проверка вживую.
- Коммиты — короткое повелительное наклонение на русском, без префиксов, без `Co-Authored-By`.

---

### Task 1: `budget_places` nullable + «нет данных» (Часть A)

**Files:**
- Modify: `src/robot/models.py` (`RobotProgram`, `ProgramState`, `DimaPrioritySnapshot`)
- Modify: `src/robot/simulator.py` (`_try_place`, `_passing_score_for_program`)
- Modify: `src/robot/format.py` (блок снимка приоритетов)
- Modify: `src/robot/fa_pool.py`, `src/robot/mpei_pool.py`, `src/robot/stankin_pool.py` (переименовать константу `DEFAULT_BUDGET_PLACES`)

**Interfaces:**
- Produces: `RobotProgram.budget_places: int | None`, `ProgramState.budget_places/remaining: int | None`, `DimaPrioritySnapshot.budget_places/remaining_at_turn: int | None`. Константа `UNTRACKED_FALLBACK_PLACES` в трёх пулах.

- [ ] **Step 1: Nullable-поля в моделях**

`src/robot/models.py`:

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

`DimaPrioritySnapshot` (строки 77-87):
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

- [ ] **Step 2: Обработка `None` в симуляторе**

`src/robot/simulator.py`, `_try_place` (строки 161-165) — программа с неизвестными местами не принимает зачислений:
```python
    for program_key in person.ordered_program_keys(phase=phase):
        state = states.get(program_key)
        if state is None or state.remaining is None or state.remaining <= 0:
            continue
        state.remaining -= 1
```

`_passing_score_for_program` (строки 208-212):
```python
def _passing_score_for_program(state: ProgramState, people_by_code: dict[str, RobotPerson]) -> int | None:
    if state.remaining is None or state.remaining > 0:
        return None
    scores = [people_by_code[code].score for code in state.enrolled if code in people_by_code and people_by_code[code].score > 0]
    return min(scores) if scores else None
```

(Построение `states` в `_simulate_two_phase` строки 265-274 не меняется: `remaining=program.budget_places` уже пробрасывает `None`.)

- [ ] **Step 3: «нет данных» в форматтере**

`src/robot/format.py`, блок снимка (строки 96-103), заменить на:
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

- [ ] **Step 4: Переименовать `DEFAULT_BUDGET_PLACES` → `UNTRACKED_FALLBACK_PLACES`, tracked-ветка даёт `None`**

`src/robot/mpei_pool.py` строка 104:
```python
UNTRACKED_FALLBACK_PLACES = 30  # аппроксимация мест ТОЛЬКО для непрофильных untracked-программ каскада (вне охвата гарантии реальных мест)
```
строки 286-288:
```python
            places = kcp_places.get(title)
            if places is None:
                places = (tracked_program.budget_places or None) if tracked_program else UNTRACKED_FALLBACK_PLACES
```

`src/robot/stankin_pool.py` строка 194:
```python
UNTRACKED_FALLBACK_PLACES = 30  # аппроксимация мест ТОЛЬКО для непрофильных untracked-программ каскада (вне охвата гарантии реальных мест)
```
строка 321 (untracked-ветка `else`):
```python
                else:
                    places = UNTRACKED_FALLBACK_PLACES
```

`src/robot/fa_pool.py` строка 35:
```python
UNTRACKED_FALLBACK_PLACES = 30  # аппроксимация мест ТОЛЬКО для непрофильных untracked-программ каталога ФА (вне охвата гарантии реальных мест)
```
строка 255:
```python
                    budget_places=places.get(title, UNTRACKED_FALLBACK_PLACES),
```

- [ ] **Step 5: Проверить вживую — обычный прогон + путь «нет данных»**

Синтетическая проверка None-пути (без сети):
```bash
cd /Users/gyattalert/work/priem-monitor
python3 -c "
from src.robot.models import RobotProgram, RobotPerson, ProgramChoice
from src.robot.simulator import _simulate_two_phase
from src.robot.format import format_robot_result
programs = [RobotProgram(key='X', title='Тест', budget_places=None, tracked_id=1)]
dima = RobotPerson(code='DIMA', score=250, consent=True, choices=[ProgramChoice(program_key='X', priority=1)])
res = _simulate_two_phase('ТестВуз', programs, [dima], dima, dima_in_pool=False, require_consent=True, from_cache=False)
out = format_robot_result(res)
assert 'нет данных' in out, out
print('OK: None-путь печатает «нет данных», без падений')
"
```
Ожидается: `OK: ...`, без исключений/`TypeError`.

- [ ] **Step 6: Commit**

```bash
cd /Users/gyattalert/work/priem-monitor
git add src/robot/models.py src/robot/simulator.py src/robot/format.py src/robot/fa_pool.py src/robot/mpei_pool.py src/robot/stankin_pool.py
git commit -m "Сделать budget_places робота nullable — показывать «нет данных» вместо выдуманного 30"
```

---

### Task 2: ФА — config до мест общего конкурса

**Files:**
- Modify: `config/programs.json` (id 54, id 78)

- [ ] **Step 1: Исправить два значения ФА**

В `config/programs.json` у записи `"id": 54` заменить `"budget_places": 89` на `87`; у `"id": 78` заменить `"budget_places": 25` на `62`. Точечно (по `"id"`), не переформатируя JSON.

Основание (офиц. КЦП ФА 2026/2027, очная, Москва; основные = КЦП−отд−особ−цел):
id 54 «Прикладное машинное обучение» 113−12−12−2=**87**; id 78 «Прикладные инф. системы в эк. и фин.» 79−8−8−1=**62**. id 13/42 уже верны (19) — не трогать.

- [ ] **Step 2: Проверить**

```bash
cd /Users/gyattalert/work/priem-monitor
python3 -c "
import json
data = json.load(open('config/programs.json'))
v = {it['id']: it.get('budget_places') for it in data if it['id'] in (13,42,54,78)}
assert v == {13:19,42:19,54:87,78:62}, v
print('OK ФА:', v)
"
```
Ожидается: `OK ФА: {13: 19, 42: 19, 54: 87, 78: 62}`.

- [ ] **Step 3: Commit**

```bash
cd /Users/gyattalert/work/priem-monitor
git add config/programs.json
git commit -m "Исправить бюджетные места ФА до мест общего конкурса по официальному КЦП (id54 89→87, id78 25→62)"
```

---

### Task 3: МЭИ — вычесть квоты в логике пула + правка config

**Files:**
- Modify: `src/robot/mpei_pool.py` (`_kcp_from_page`)
- Modify: `config/programs.json` (id 7, 22, 23, 35, 45)

**Interfaces:**
- Consumes: `UNTRACKED_FALLBACK_PLACES` из Task 1 (не меняется здесь).

- [ ] **Step 1: `_kcp_from_page` возвращает основные места**

`src/robot/mpei_pool.py`, в `_kcp_from_page` (строки 144-146) блок извлечения числа заменить. Было:
```python
        numbers = [c.get_text(strip=True) for c in cells if c.get_text(strip=True).isdigit()]
        if numbers:
            result.setdefault(current_title, int(numbers[0]))
```
Стало (основные = Всего − особая − отдельная − целевая; колонки таблицы: `numbers[0]`=Всего, `[1]`=особая, `[2]`=отдельная, `[3]`=целевая):
```python
        numbers = [int(c.get_text(strip=True)) for c in cells if c.get_text(strip=True).isdigit()]
        if len(numbers) >= 4:
            osnovnye = numbers[0] - numbers[1] - numbers[2] - numbers[3]
            if osnovnye > 0:
                result.setdefault(current_title, osnovnye)
```
Обновить и докстринг метода (строки 108-116): вместо «Число мест — первая числовая ячейка … колонка «Всего»» написать, что берутся основные места общего конкурса = Всего − особая квота − отдельная квота − целевая квота (колонки 0−1−2−3), т.к. робот моделирует общий конкурс.

- [ ] **Step 2: Исправить config МЭИ до основных мест**

В `config/programs.json` заменить `budget_places` у записей МЭИ: id 7 `20`→`13`, id 22 `100`→`36`, id 23 `30`→`23`, id 35 `50`→`38`, id 45 `201`→`130`. Точечно по `"id"`.

Основание (офиц. kcp-table МЭИ 2026, основные = Всего−особ−отд−цел): id7 20−2−2−3=13; id22 50−5−5−4=36; id23 30−3−3−1=23; id35 50−5−5−2=38; id45 185−19−19−17=130.

- [ ] **Step 3: Проверить вживую — `_kcp_from_page` даёт основные**

```bash
cd /Users/gyattalert/work/priem-monitor
python3 -c "
from src.robot.mpei_pool import fetch_kcp_places
kcp = fetch_kcp_places()
# основные для известных групп: Прикладная информатика=23, ИВТ=130
pi = next((v for k,v in kcp.items() if 'Прикладная информатика' in k), None)
ivt = next((v for k,v in kcp.items() if 'Информатика и вычислительная техника' in k), None)
print('Прикладная информатика ->', pi, '| ИВТ ->', ivt)
assert pi == 23, pi
assert ivt == 130, ivt
print('OK: _kcp_from_page возвращает основные места')
"
```
Ожидается: `Прикладная информатика -> 23 | ИВТ -> 130`, `OK: ...`. (Если сайт МЭИ обновит КЦП и числа сместятся — сверить с формулой Всего−особ−отд−цел по живой таблице, а не считать шаг проваленным вслепую.)

Проверка config:
```bash
cd /Users/gyattalert/work/priem-monitor
python3 -c "
import json
data = json.load(open('config/programs.json'))
v = {it['id']: it.get('budget_places') for it in data if it['id'] in (7,22,23,35,45)}
assert v == {7:13,22:36,23:23,35:38,45:130}, v
print('OK МЭИ config:', v)
"
```

- [ ] **Step 4: Commit**

```bash
cd /Users/gyattalert/work/priem-monitor
git add src/robot/mpei_pool.py config/programs.json
git commit -m "МЭИ — считать места общего конкурса (КЦП минус квоты) в _kcp_from_page и config"
```

---

### Task 4: СТАНКИН — override реальными основными местами

**Files:**
- Modify: `src/robot/stankin_pool.py` (константа-мапа + резолв мест)

**Interfaces:**
- Consumes: `UNTRACKED_FALLBACK_PLACES` из Task 1.

- [ ] **Step 1: Добавить `STANKIN_KCP_OVERRIDES`**

`src/robot/stankin_pool.py`, рядом с `UNTRACKED_FALLBACK_PLACES` (после строки 194) добавить:
```python
# Реальные места ОБЩЕГО КОНКУРСА (КЦП − особая − отдельная − целевая квота) по
# отслеживаемым конкурсным группам, с priem.stankin.ru (training_programs +
# страницы квот), 2026/2027, очная. Ключ — строка направления из каталога.
# Живая nap-страница даёт полный КЦП с квотами (завышает), поэтому для
# отслеживаемых групп берём эти официальные основные места напрямую.
STANKIN_KCP_OVERRIDES: dict[str, int] = {
    "09.03.04 Программная инженерия": 47,
    "09.03.03.01 Математическое и компьютерное моделирование процессов и систем": 40,
    "09.03.03.02 Управление данными": 57,
    "09.03.01.01 Разработка программных комплексов": 19,
    "09.03.02.01 Разработка и внедрение корпоративных информационных систем": 40,
}
```

- [ ] **Step 2: Использовать override раньше nap-тотала и суммы**

`src/robot/stankin_pool.py`, резолв мест в цикле (строки 304-321), заменить:
```python
            places = kcp_places.get(direction)
            if places is None:
                if tracked_key:
                    configs = direction_groups[direction]
                    places = sum(p.budget_places for p in configs)
```
на:
```python
            places = STANKIN_KCP_OVERRIDES.get(direction)
            if places is None:
                places = kcp_places.get(direction)
            if places is None:
                if tracked_key:
                    configs = direction_groups[direction]
                    places = sum(p.budget_places for p in configs)
```
(остальные ветки `if len(configs) > 1: ... print(...)` и `else: places = UNTRACKED_FALLBACK_PLACES`, и строка `programs.append(...)` — без изменений.)

- [ ] **Step 3: Проверить вживую — отслеживаемые группы получают основные места**

```bash
cd /Users/gyattalert/work/priem-monitor
python3 -c "
from src.robot.stankin_pool import StankinFullPool
people, programs, fetched_at, from_cache = StankinFullPool().build(use_cache=False)
by_title = {p.title: p.budget_places for p in programs if p.tracked_id is not None}
print('tracked places:', by_title)
exp = {
  '09.03.04 Программная инженерия': 47,
  '09.03.03.01 Математическое и компьютерное моделирование процессов и систем': 40,
  '09.03.03.02 Управление данными': 57,
  '09.03.01.01 Разработка программных комплексов': 19,
  '09.03.02.01 Разработка и внедрение корпоративных информационных систем': 40,
}
for k,v in exp.items():
    got = by_title.get(k)
    assert got == v, f'{k}: ожидалось {v}, получено {got} (проверь совпадение строки направления с каталогом)'
print('OK: все 5 отслеживаемых групп СТАНКИНа = реальные основные места')
"
```
Ожидается: `OK: ...`. Если какая-то группа не совпала (ключ override не равен строке направления из каталога) — вывести фактический `by_title`, поправить ключ в `STANKIN_KCP_OVERRIDES` под реальную строку каталога, не считать шаг пройденным. (Сборка тянет живые списки — может занять 1-2 мин.)

- [ ] **Step 4: Commit**

```bash
cd /Users/gyattalert/work/priem-monitor
git add src/robot/stankin_pool.py
git commit -m "СТАНКИН — брать реальные места общего конкурса по группам (override), а не полный КЦП/сумму"
```

- [ ] **Step 5: Закрыть задачи beads**

```bash
~/.local/bin/bd close priem-monitor-q1d --reason "Убрана выдуманная 30 из tracked-ветки, budget_places nullable, «нет данных» вместо заглушки"
~/.local/bin/bd close priem-monitor-v0j --reason "Места приведены к общему конкурсу по всем 4 вузам: МИРЭА верно; ФА config (id54→87,id78→62); МЭИ логика+config (13/36/23/38/130); СТАНКИН override (47/40/57/19/40)"
```

## Self-Review

- **Spec coverage:** nullable/«нет данных» — Task 1; убрать 30 — Task 1 Step 4; ФА config — Task 2; МЭИ логика+config — Task 3; СТАНКИН override — Task 4; МИРЭА не трогаем — подтверждено (нет в файлах). Все разделы Части B покрыты.
- **Placeholder scan:** плейсхолдеров нет; код реальный.
- **Type consistency:** `budget_places/remaining: int | None` согласованы между моделями (Task 1 Step 1), симулятором (Step 2), форматтером (Step 3); `UNTRACKED_FALLBACK_PLACES` единообразно (Task 1 Step 4), используется без изменений в Task 3/4; `STANKIN_KCP_OVERRIDES: dict[str, int]` определён и использован в Task 4. Числа сверены со спекой.
