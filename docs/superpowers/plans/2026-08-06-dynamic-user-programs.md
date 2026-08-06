# Динамические специальности и валидация кода — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Спека:** `docs/superpowers/specs/2026-08-06-dynamic-user-programs-design.md`

**Goal:** Специальности, места, проходные и порядок приоритетов подтягиваются
под конкретный код пользователя из конкурсных списков, а не берутся из
фиксированного списка в `config/programs.json`. Плюс бот перестаёт принимать
несуществующие коды.

**Architecture:** Каскад уже считает по всем выборам человека — конфиг
использовался ровно в одном месте, чтобы **урезать** выборы пользователя.
Основная работа не в расчёте, а в отображении и в командах, которые адресуют
направление числовым кодом из конфига.

**Tech Stack:** Python 3, существующие модули робота. Новых зависимостей нет.

## Global Constraints

- Тестов в проекте нет. Приёмка каждой задачи — живой прогон конкретной команды
  с конкретным ожидаемым выводом.
- Проверка кода при регистрации идёт ТОЛЬКО по кэшу. Сетевой запрос в этом
  пути запрещён: пересборка ФА и СТАНКИНа — по 3,5 минуты, бот на это время
  перестанет отвечать всем чатам.
- Сохранённый порядок приоритетов может только **переупорядочивать** реальные
  направления. Дописать направление, куда человек не подавал, нельзя ни при
  каком раскладе (`require_in_pool=True`).
- Комментарии в коде — по-русски, объясняют ПОЧЕМУ.
- Коммиты конвенциональные, без `Co-Authored-By`.

## Опорные коды для проверок (живые кэши на 2026-08-06)

| код | чем полезен |
|---|---|
| `1616947` | МИРЭА, ни одной специальности из конфига — 5 своих направлений |
| `1514555` | МЭИ, 4 из 5 конфиговых + свои; есть и в МИРЭА; нет в СТАНКИНе и ФА |
| `1824102` | есть в МЭИ, МИРЭА, СТАНКИНе, Политехе |
| `999999999` | не существует нигде |

Покрытие данных (проверено): места живьём и проходные сайта есть у 100%
направлений МИРЭА (84), МЭИ (27), СТАНКИНа (21), Политеха (65); у ФА — 40 из 45,
и все 5 исключений имеют **ноль абитуриентов**.

---

### Задача 1: валидация кода при регистрации и поимённый `/статус`

Полностью независима от остальных задач плана: новый модуль плюс правки в
`scripts/telegram_bot.py`. Ни одного общего файла с задачами 2–4.

**Files:**
- Create: `src/robot/code_lookup.py`
- Modify: `scripts/telegram_bot.py` (регистрация кода, `_format_multi_status`)

**Interfaces:**
- Produces: `CodePresence`, `find_code_presence(code: str) -> CodePresence`

- [ ] **Шаг 1: `code_lookup.py`**

```python
@dataclass
class CodePresence:
    found: list[str]      # вузы, где код есть в конкурсных списках
    absent: list[str]     # пул прочитан, кода нет
    unchecked: list[str]  # кэш не прочитался — судить не можем

    @property
    def is_valid(self) -> bool:
        return bool(self.found)
```

`find_code_presence` идёт по `robot_ready_universities()`, берёт
`read_cached_pool(parser)` и ищет `person.code == code`. `read_cached_pool`
возвращает `None`, когда кэша нет — это `unchecked`, НЕ `absent`. Разница
принципиальна: на ней стоит решение отвергнуть код или нет.

- [ ] **Шаг 2: приёмка модуля**

```bash
python3 -c "
import sys; sys.path.insert(0,'.')
import time
from src.robot.code_lookup import find_code_presence
for code in ['1824102','1514555','1616947','999999999']:
    t=time.time(); p=find_code_presence(code)
    print(f'{code}: found={p.found} absent={len(p.absent)} unchecked={len(p.unchecked)} valid={p.is_valid} ({time.time()-t:.2f} c)')
"
```

Ожидается: `1824102` — найден в нескольких вузах; `1514555` — в МЭИ и МИРЭА;
`1616947` — в МИРЭА; `999999999` — `found=[]`, `is_valid=False`.
Время на код — меньше секунды.

- [ ] **Шаг 3: регистрация в `telegram_bot.py`**

Обе точки входа — `/код <n>` и голое число от незарегистрированного — после
`looks_like_code` зовут `find_code_presence`:

| ситуация | действие |
|---|---|
| `found` не пуст | сохранить; в ответе назвать, где участвует и где нет |
| `found` пуст, `unchecked` пуст | **не сохранять**; сказать, что код не найден ни в одном вузе |
| `found` пуст, `unchecked` не пуст | сохранить с оговоркой про непроверенные вузы |

Комментарий обязателен: отказ только по полным основаниям, иначе прогрев
кэшей после рестарта запирал бы снаружи живого пользователя.

- [ ] **Шаг 4: `_format_multi_status` называет все вузы**

Вместо молчаливого `if result.error: continue`:

| состояние | строка |
|---|---|
| код найден, проходит / не проходит | как сейчас |
| кода в вузе нет | `— Вуз —` + «Вы не участвуете в этом вузе» |
| `POOL_NOT_READY_ERROR` | `— Вуз —` + «⏳ данные ещё собираются» |
| `config_error` | не показывать вовсе |

- [ ] **Шаг 5: приёмка**

```bash
python3 -c "
import sys, importlib.util; sys.path.insert(0,'.')
from src.robot.simulator import run_robot_simulation
from src.robot.universities import robot_ready_universities
from src.telegram_users import build_robot_settings
spec = importlib.util.spec_from_file_location('tb','scripts/telegram_bot.py')
tb = importlib.util.module_from_spec(spec); spec.loader.exec_module(tb)
code='1514555'
res=[(u, run_robot_simulation(u, settings=build_robot_settings(code,u), stale_ok=True))
     for u in sorted(robot_ready_universities())]
print(tb._format_multi_status(code,res))
"
```

Ожидается: в выводе присутствуют **все** готовые вузы поимённо; про СТАНКИН и
ФА сказано «не участвуете», а не молчание.

Отдельно проверить руками в боте: `/код 999999999` не сохраняется (файл
`data/telegram_users.json` не меняется), `/код 1616947` сохраняется.

- [ ] **Шаг 6: коммит**

```bash
git add src/robot/code_lookup.py scripts/telegram_bot.py
git commit -m "feat(bot): требовать существующий код при регистрации и называть все вузы в статусе"
```

---

### Задача 2: ядро динамики — специальности из пула

**Files:**
- Modify: `src/robot/simulator.py`
- Modify: `src/robot/models.py`
- Modify: `src/robot/verification.py`

**Interfaces:**
- Produces: `RobotSimulationResult.user_programs` (замена `tracked_programs`)

- [ ] **Шаг 1: убрать `_filter_choices_to_tracked`**

Метод удаляется целиком. `_resolve_dima_person` отдаёт найденного человека как
есть, со всеми его выборами.

Внимание: у метода была аварийная ветка «пересечение пусто → вернуть все
выборы». Именно она случайно доказала работоспособность динамики (код
`1616947` считался корректно). Теперь это становится штатным путём для всех.

- [ ] **Шаг 2: `require_in_pool=True`**

В `_resolve_dima_person` вызов `_apply_priority_order(..., require_in_pool=False)`
меняется на `True`.

**Это надо проверить живьём, а не на рассуждении.** Флаг стоял `False`
намеренно: комментарий объясняет, что человека, вычеркнутого пометкой
«Зачисляется в другой КГ», всё равно нужно показать. Разбор говорит, что
переход безопасен — такие выборы остаются в `choices` с флагом
`enrolls_elsewhere`, отсекаются только в `ordered_program_keys`, а `pool_keys`
строится по `choices` целиком. Шаг 5 это проверяет.

- [ ] **Шаг 3: `tracked_programs` → `user_programs`**

В `RobotSimulationResult` переименовать поле и заполнять его по выборам
человека, а не по `tracked_id is not None`. Обновить всех потребителей
(`format.py`, `telegram_bot.py`).

- [ ] **Шаг 4: сверка по направлениям пользователя**

`verification.build_verification_report` строит `seats` по `user_programs`.
Направление с `passing_cutoff is None` даёт статус `unavailable`, а не
`mismatch` — иначе филиальные программы ФА без оракула читались бы как ошибка
модели.

- [ ] **Шаг 5: приёмка**

```bash
python3 -c "
import sys; sys.path.insert(0,'.')
from src.telegram_users import build_robot_settings
from src.robot.simulator import run_robot_simulation
for uni, code in [('МИРЭА','1616947'), ('МЭИ','1514555'), ('МЭИ','1824102')]:
    r = run_robot_simulation(uni, settings=build_robot_settings(code,uni), stale_ok=True)
    print(f'{uni} {code}: error={r.error} приоритетов={len(r.dima_remaining_at_turn)}')
    for s in r.dima_remaining_at_turn: print('   ', s.priority, s.title[:55])
"
```

Ожидается: `1616947` в МИРЭА — 5 своих направлений (Компьютерная безопасность,
Картография, Физика и т.д.), `error=None`. `1514555` в МЭИ — **все** его
направления МЭИ, а не 4 пересечения с конфигом.

Проверка шага 2 отдельно:

```bash
python3 -c "
import sys; sys.path.insert(0,'.')
from src.robot.universities import read_cached_pool
people, programs, _, _ = read_cached_pool('mpei')
who = [p for p in people if any(getattr(c,'enrolls_elsewhere',False) for c in p.choices)]
print('людей с enrolls_elsewhere в МЭИ:', len(who))
print('пример кода:', who[0].code if who else 'нет')
"
```

Взять этот код, прогнать симуляцию с сохранённым порядком и убедиться, что
направление с `enrolls_elsewhere` из показа НЕ пропало. Если пропало —
`require_in_pool` вернуть в `False` и доложить.

- [ ] **Шаг 6: коммит**

```bash
git add src/robot/simulator.py src/robot/models.py src/robot/verification.py
git commit -m "feat(robot): специальности пользователя брать из конкурсных списков, а не из конфига"
```

---

### Задача 3: отображение — код ОКСО из пула и адресация по приоритету

**Files:**
- Modify: `src/robot/models.py` (`RobotProgram.okso_code`)
- Modify: `src/robot/mirea_pool.py`, `mpei_pool.py`, `stankin_pool.py`,
  `fa_pool.py`, `polytech_pool.py`
- Modify: `src/robot/format.py`
- Modify: `scripts/telegram_bot.py` (`/конкуренты`)

- [ ] **Шаг 1: поле `okso_code`**

`RobotProgram.okso_code: str | None = None` с комментарием, зачем: старый
`format._okso_by_tracked_id()` строится по конфигу и работает только для
отслеживаемых, а направления теперь произвольные.

- [ ] **Шаг 2: заполнить в пулах**

Код уже есть в заголовках, доставать надо по-разному:

| пул | где код | пример |
|---|---|---|
| МИРЭА | в хвосте заголовка, в скобках | `Анализ данных (01.03.04 Прикладная математика)` |
| МЭИ | там же | `Фундаментальная информатика … (02.03.02 …)` |
| СТАНКИН | в начале заголовка | `09.03.01 Информатика и вычислительная техника` |
| Политех | в шапке списка, отдельная ячейка | `09.03.01.07` |
| ФА | **кода нет** | `Бизнес-информатика, Бакалавр, …, Очная` → `None` |

У ФА подставлять код по нечёткому совпадению названий **запрещено**: лучше
показать направление без кода, чем приписать ему чужой.

- [ ] **Шаг 3: `format.py`**

`_okso_by_tracked_id()` удаляется, код берётся из `program.okso_code`. Строка
приоритета печатает порядковый номер как адресуемый идентификатор:
`3. 09.03.02 Название … (обратиться: /конкуренты МЭИ 3)`.

- [ ] **Шаг 4: `/конкуренты` по номеру приоритета**

`format_competitors(result, priority_number)` ищет направление по позиции в
списке приоритетов пользователя, а не по `tracked_id`. Тексты подсказок в
`_welcome`, `_help_text` и в сообщении об ошибке — обновить.

- [ ] **Шаг 5: приёмка**

```bash
python3 -c "
import sys; sys.path.insert(0,'.')
from src.telegram_users import build_robot_settings
from src.robot.simulator import run_robot_simulation
from src.robot.format import format_robot_result, format_competitors
r = run_robot_simulation('МЭИ', settings=build_robot_settings('1824102','МЭИ'), stale_ok=True)
print(format_robot_result(r)[:1200])
print('--- конкуренты по 3-му приоритету ---')
print(format_competitors(r, 3)[:600])
"
```

Ожидается: у каждого направления МЭИ напечатан код ОКСО; `/конкуренты 3`
показывает конкурентов именно по третьему приоритету. Прогнать то же по ФА и
убедиться, что отсутствие кода не ломает вывод.

- [ ] **Шаг 6: коммит**

```bash
git add src/robot/models.py src/robot/*_pool.py src/robot/format.py scripts/telegram_bot.py
git commit -m "feat(robot): код ОКСО из пула и адресация направления по номеру приоритета"
```

---

### Задача 4: приоритеты по ключам направлений

**Files:**
- Modify: `src/robot/priorities.py`
- Modify: `src/robot/telegram_priorities.py`

- [ ] **Шаг 1: хранить ключи, а не id конфига**

`get_saved_priority_ids` / `save_priority_ids` переходят с `program_id` из
`config/programs.json` на `RobotProgram.key`. Переименовать соответственно
(`get_saved_priority_keys` / `save_priority_keys`).

- [ ] **Шаг 2: сброс старых сохранённых значений**

Старые записи не конвертируются, а игнорируются: числовой id, не совпавший ни с
одним ключом пула, просто отбрасывается. Основание — сохранённый порядок есть
только у одного чата, а порядок по умолчанию теперь и так настоящий, с сайта.
Конвертер ради одной записи, которая и без него станет верной, писать незачем.

- [ ] **Шаг 3: редактор листает реальные направления**

`telegram_priorities.py` перечисляет направления пользователя из пула в его
настоящем порядке, а не программы из конфига.

- [ ] **Шаг 4: приёмка**

```bash
python3 -c "
import sys; sys.path.insert(0,'.')
from src.telegram_users import build_robot_settings
from src.robot.simulator import run_robot_simulation
from src.robot.universities import read_cached_pool
people, programs, _, _ = read_cached_pool('mpei')
me = next(p for p in people if p.code=='1824102')
keys = [c.program_key for c in sorted(me.choices, key=lambda c: c.priority)]
rev = list(reversed(keys))
r = run_robot_simulation('МЭИ', settings=build_robot_settings('1824102','МЭИ'),
                         stale_ok=True, priority_ids=rev)
print('обратный порядок применился:', [s.title[:30] for s in r.dima_remaining_at_turn])
bogus = rev + ['несуществующий-ключ']
r2 = run_robot_simulation('МЭИ', settings=build_robot_settings('1824102','МЭИ'),
                          stale_ok=True, priority_ids=bogus)
print('чужой ключ не добавился:', len(r2.dima_remaining_at_turn) == len(r.dima_remaining_at_turn))
"
```

Ожидается: порядок переставился; чужой ключ проигнорирован, число направлений
не выросло.

Отдельно проверить руками в боте: `/приоритет МЭИ` предлагает переставить
реальные направления пользователя.

- [ ] **Шаг 5: коммит**

```bash
git add src/robot/priorities.py src/robot/telegram_priorities.py
git commit -m "feat(robot): порядок приоритетов хранить ключами направлений пула"
```
