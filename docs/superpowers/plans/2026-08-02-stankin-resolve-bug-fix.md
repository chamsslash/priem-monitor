# Починка STANKIN_PROGRAMS resolve-бага — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `_resolve_program()` перестаёт склеивать id44 с id47 и id61 с id62 в один конкурсный список — обычный дашборд и робот-пул СТАНКИНа считают каждое направление по своему реальному, отдельному списку абитуриентов и своей реальной квоте.

**Architecture:** Точечная правка данных в двух словарях: `STANKIN_PROGRAMS` (исправить 2 неверных значения) и `STANKIN_KCP_OVERRIDES` (добавить 2 новые записи для общих профилей). Никакой новой инфраструктуры — каталог уже умеет фетчить оба списка раздельно (`FALLBACK_CATALOG` уже содержит обе строки направления).

**Tech Stack:** Python 3.12. Без новых зависимостей.

## Global Constraints

- Спека: `docs/superpowers/specs/2026-08-02-stankin-resolve-bug-design.md`. Задача beads: `priem-monitor-vr9` (discovered-from `priem-monitor-v0j`).
- Баг общий для двух потребителей `_resolve_program()`: `StankinParser.fetch()` (обычный дашборд/бот) и `stankin_pool.py::_tracked_direction_groups()` (робот) — правка одной функции чинит оба пути одновременно.
- Официальные числа (подтверждены трижды независимо — training_programs + отдельные страницы особая/отдельная/целевая квоты): «09.03.01 Информатика и вычислительная техника» основные=45 (70−7−7−11); «09.03.02 Информационные системы и технологии» основные=53 (78−8−8−9).
- Не трогаем: инфраструктуру каталога/фетча, fallback по `direction_code` (строки 133-138 remaining.py), config СТАНКИНа (числа id44=45/id61=53 там уже верны), остальные направления и вузы.
- В проекте нет pytest для парсеров/пулов — проверка вживую.
- Коммит — короткое повелительное наклонение на русском, без префиксов, без `Co-Authored-By`.

---

### Task 1: Исправить STANKIN_PROGRAMS + добавить override для общих профилей

**Files:**
- Modify: `src/parsers/remaining.py:76-83` (`StankinParser.STANKIN_PROGRAMS`)
- Modify: `src/robot/stankin_pool.py:200-206` (`STANKIN_KCP_OVERRIDES`)

- [ ] **Step 1: Исправить два значения в `STANKIN_PROGRAMS`**

В `src/parsers/remaining.py`, текущий блок (строки 75-83):

```python
    STANKIN_PROGRAMS = {
        "программная инженерия": "09.03.04 Программная инженерия",
        "разработка программных комплексов в рамках": "09.03.01.01 Разработка программных комплексов",
        "разработка программных комплексов": "09.03.01.01 Разработка программных комплексов",
        "математическое и компьютерное моделирование": "09.03.03.01 Математическое и компьютерное моделирование процессов и систем",
        "управление данными": "09.03.03.02 Управление данными",
        "цифровые системы управления": "09.03.02.01 Разработка и внедрение корпоративных информационных систем",
        "разработка и внедрение корпоративных": "09.03.02.01 Разработка и внедрение корпоративных информационных систем",
    }
```

Заменить на (изменились только строки для ключей «...в рамках» и «цифровые системы управления»):

```python
    STANKIN_PROGRAMS = {
        "программная инженерия": "09.03.04 Программная инженерия",
        "разработка программных комплексов в рамках": "09.03.01 Информатика и вычислительная техника",
        "разработка программных комплексов": "09.03.01.01 Разработка программных комплексов",
        "математическое и компьютерное моделирование": "09.03.03.01 Математическое и компьютерное моделирование процессов и систем",
        "управление данными": "09.03.03.02 Управление данными",
        "цифровые системы управления": "09.03.02 Информационные системы и технологии",
        "разработка и внедрение корпоративных": "09.03.02.01 Разработка и внедрение корпоративных информационных систем",
    }
```

- [ ] **Step 2: Добавить 2 записи в `STANKIN_KCP_OVERRIDES`**

В `src/robot/stankin_pool.py`, текущий блок (строки 200-206):

```python
STANKIN_KCP_OVERRIDES: dict[str, int] = {
    "09.03.04 Программная инженерия": 47,
    "09.03.03.01 Математическое и компьютерное моделирование процессов и систем": 40,
    "09.03.03.02 Управление данными": 57,
    "09.03.01.01 Разработка программных комплексов": 19,
    "09.03.02.01 Разработка и внедрение корпоративных информационных систем": 40,
}
```

Заменить на (добавлены 2 новые строки для общих профилей):

```python
STANKIN_KCP_OVERRIDES: dict[str, int] = {
    "09.03.04 Программная инженерия": 47,
    "09.03.03.01 Математическое и компьютерное моделирование процессов и систем": 40,
    "09.03.03.02 Управление данными": 57,
    "09.03.01 Информатика и вычислительная техника": 45,
    "09.03.01.01 Разработка программных комплексов": 19,
    "09.03.02 Информационные системы и технологии": 53,
    "09.03.02.01 Разработка и внедрение корпоративных информационных систем": 40,
}
```

- [ ] **Step 3: Проверить вживую — `_resolve_program` больше не склеивает пары**

```bash
cd /Users/gyattalert/work/priem-monitor
python3 -c "
from src.config_loader import load_programs
from src.parsers.remaining import StankinParser

parser = StankinParser()
programs = {p.id: p for p in load_programs() if p.id in (44, 47, 61, 62)}
results = {}
for pid in (44, 47, 61, 62):
    results[pid] = parser._resolve_program(programs[pid])
    print('id', pid, '->', results[pid])

assert results[44] != results[47], 'id44 и id47 всё ещё дают одну строку'
assert results[61] != results[62], 'id61 и id62 всё ещё дают одну строку'
assert results[44] == '09.03.01 Информатика и вычислительная техника', results[44]
assert results[47] == '09.03.01.01 Разработка программных комплексов', results[47]
assert results[61] == '09.03.02 Информационные системы и технологии', results[61]
assert results[62] == '09.03.02.01 Разработка и внедрение корпоративных информационных систем', results[62]
print('OK: все 4 id резолвятся в свои собственные, различные строки направления')
"
```

Ожидается: 4 разные строки в выводе (id44 ≠ id47, id61 ≠ id62), `OK: ...` в конце, без `AssertionError`.

- [ ] **Step 4: Проверить вживую — живой список абитуриентов для id44 отличается от id47**

```bash
cd /Users/gyattalert/work/priem-monitor
python3 -c "
from src.config_loader import load_programs
from src.parsers.remaining import StankinParser

parser = StankinParser()
programs = {p.id: p for p in load_programs() if p.id in (44, 47)}
result_44 = parser.fetch(programs[44])
result_47 = parser.fetch(programs[47])
print('id44: error=', result_44.error, '| applicants=', len(result_44.applicants))
print('id47: error=', result_47.error, '| applicants=', len(result_47.applicants))
assert result_44.error is None, result_44.error
assert result_47.error is None, result_47.error
assert len(result_44.applicants) > 0 and len(result_47.applicants) > 0
print('OK: оба направления вернули непустые списки без ошибок')
"
```

Ожидается: `error=None` у обоих, непустые списки, `OK: ...`. (Полное побайтовое сравнение списков не требуется — раздельность источников подтверждена на этапе спеки прямым запросом к сайту; здесь достаточно убедиться, что оба живых фетча через сам класс `StankinParser` проходят без ошибок после правки словаря.)

- [ ] **Step 5: Проверить вживую — полная сборка пула даёт правильные места и не пишет warning про смёрженную пару**

```bash
cd /Users/gyattalert/work/priem-monitor
python3 -c "
import io, sys, contextlib
from src.robot.stankin_pool import StankinFullPool

stderr_capture = io.StringIO()
with contextlib.redirect_stderr(stderr_capture):
    people, programs, fetched_at, from_cache = StankinFullPool().build(use_cache=False)

by_id = {p.tracked_id: p.budget_places for p in programs if p.tracked_id is not None}
print('tracked places by id:', by_id)
exp = {33: 47, 44: 45, 47: 19, 55: 40, 56: 57, 61: 53, 62: 40}
for pid, expected in exp.items():
    got = by_id.get(pid)
    assert got == expected, f'id{pid}: ожидалось {expected}, получено {got}'

stderr_text = stderr_capture.getvalue()
assert 'смёрженной пары' not in stderr_text, f'предупреждение про смёрженную пару всё ещё печатается: {stderr_text}'
print('OK: все 7 отслеживаемых id СТАНКИНа получили верные места, warning про смёрженную пару не печатается')
"
```

Ожидается: `tracked places by id: {33: 47, 44: 45, 47: 19, 55: 40, 56: 57, 61: 53, 62: 40}`, `OK: ...`. (Сборка живая, тянет реальные списки — может занять 1-3 минуты, это нормально.)

- [ ] **Step 6: Commit**

```bash
cd /Users/gyattalert/work/priem-monitor
git add src/parsers/remaining.py src/robot/stankin_pool.py
git commit -m "Починить STANKIN_PROGRAMS — id44/id61 больше не склеиваются с id47/id62 в общий список"
```

- [ ] **Step 7: Закрыть задачу в beads**

```bash
~/.local/bin/bd close priem-monitor-vr9 --reason "STANKIN_PROGRAMS больше не склеивает общий профиль с ТОП-ИТ профилем; добавлены override для 09.03.01/09.03.02 (45/53); проверено вживую — оба потребителя (дашборд и робот) резолвят id44/id61 в собственные, отдельные строки направления"
```

## Self-Review

- **Spec coverage:** правка `STANKIN_PROGRAMS` (2 значения) — Step 1; правка `STANKIN_KCP_OVERRIDES` (+2 записи) — Step 2; проверка что оба потребителя (`_resolve_program` напрямую + `StankinParser.fetch()`) работают раздельно — Steps 3-4; полная сборка пула с верными числами и отсутствием ложного warning — Step 5. Всё покрыто.
- **Placeholder scan:** плейсхолдеров/TODO нет; весь код реальный, с точными ожидаемыми значениями.
- **Type consistency:** ключи `STANKIN_KCP_OVERRIDES` (`dict[str, int]`) и значения `STANKIN_PROGRAMS` (`dict[str, str]`) используют идентичные строки направлений между двумя файлами (сверено посимвольно: `"09.03.01 Информатика и вычислительная техника"`, `"09.03.02 Информационные системы и технологии"`) — иначе override не сработает по несовпадению ключа.
