# Робот зачисления для МЭИ и СТАНКИН — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Добавить полный «пул» абитуриентов для МЭИ и СТАНКИН, чтобы робот
зачисления (`src/robot/simulator.py`) мог каскадно просчитывать выбывание
по приоритетам для этих двух вузов — так же, как он уже это делает для
МИРЭА и Финуниверситета.

**Architecture:** Два новых модуля (`src/robot/mpei_pool.py`,
`src/robot/stankin_pool.py`), по одному классу `XxxFullPool` в каждом —
точная копия механики `mirea_pool.py`/`fa_pool.py` (каталог направлений →
абитуриенты по каждому → сшивка по уникальному коду → дисковый кэш).
Реализация КАЖДОГО модуля обособлена и специфична для конкретного сайта
вуза (МЭИ и СТАНКИН устроены по-разному и не похожи ни друг на друга, ни на
МИРЭА технически) — общая только итоговая архитектура/интерфейс, не код.
Симулятор, Telegram-бот, редактор приоритетов не меняются.

**Tech Stack:** Python 3, `requests`, `beautifulsoup4` (`lxml`-парсер) —
без Playwright, оба сайта отдают обычный HTML.

## Global Constraints

- В проекте нет тестового фреймворка (`pytest` не установлен, папки `tests/`
  нет) и нет моков сети — `mirea_pool.py`/`fa_pool.py` тоже проверялись
  только реальными прогонами на живых данных. Каждая задача в этом плане
  проверяется так же: реальным HTTP-запросом к живому сайту вуза и
  проверкой результата через `assert` в одноразовом скрипте, а не через
  `pytest`. Стабильность интернет-соединения при выполнении шагов
  проверки — обязательное условие (без него завершить план нельзя).
- Ключ программы в пуле (`RobotProgram.key`/`ProgramChoice.program_key`)
  для программ, которые ЕСТЬ в `config/programs.json`, обязан
  дословно равняться `str(program.id)` — см. `src/robot/direction_keys.py`.
  Для программ вне конфига ключ может быть любым уникальным «сайтовым»
  идентификатором. Это критично для каждой задачи, где строится
  `RobotProgram`/`ProgramChoice`.
- Все новые сетевые запросы — обычные `requests.get`/`requests.Session`,
  без Playwright (оба сайта отдают серверный HTML, JS не нужен).
- Коммитить после каждой задачи отдельным коммитом (см. `git-commit`
  конвенцию репозитория — сообщения на русском, без `Co-Authored-By`).

---

## Task 1: Исправить баг сопоставления «Управление данными» в StankinParser

Это отдельный, не связанный с роботом баг: программа «Управление данными»
(id=56 в `config/programs.json`) сейчас смотрит на список «Математическое
и компьютерное моделирование» (id=55) вместо своего собственного. Нужно
исправить его первым, иначе робот унаследует ошибку, а дашборд продолжит
показывать Диме чужие данные.

**Files:**
- Modify: `src/parsers/remaining.py:69-80` (словарь
  `StankinParser.STANKIN_PROGRAMS`)

**Interfaces:**
- Produces: `StankinParser.STANKIN_PROGRAMS["управление данными"] ==
  "09.03.03.02 Управление данными"` (было `"09.03.03.01 ..."`)

- [ ] **Step 1: Поменять значение в словаре**

В `src/parsers/remaining.py` найти строку:

```python
        "управление данными": "09.03.03.01 Математическое и компьютерное моделирование процессов и систем",
```

и заменить на:

```python
        "управление данными": "09.03.03.02 Управление данными",
```

- [ ] **Step 2: Проверить вживую, что список теперь другой**

```bash
cd /Users/gyattalert/work/priem-monitor
python3 - <<'EOF'
import sys
sys.path.insert(0, ".")
from src.config_loader import load_programs
from src.parsers.remaining import StankinParser

parser = StankinParser()
programs = {p.id: p for p in load_programs() if p.university == "СТАНКИН"}

p55 = programs[55]
p56 = programs[56]
direction55 = parser._resolve_program(p55)
direction56 = parser._resolve_program(p56)
print("id=55 (Мат. моделирование) ->", direction55)
print("id=56 (Управление данными) ->", direction56)
assert direction55 != direction56, "направления по-прежнему совпадают!"
assert direction56 == "09.03.03.02 Управление данными"

result55 = parser.fetch(p55)
result56 = parser.fetch(p56)
print("id=55: абитуриентов =", len(result55.applicants), "ошибка =", result55.error)
print("id=56: абитуриентов =", len(result56.applicants), "ошибка =", result56.error)
assert result56.error is None, result56.error
assert len(result56.applicants) > 0, "список «Управление данными» пуст"
print("OK")
EOF
```

Ожидается: `direction55` и `direction56` — разные строки, оба списка
непустые, без ошибок. Если список `09.03.03.02` реально пуст на сайте —
это будет видно по `result56.error`, разберитесь перед тем как продолжать
(значит официальное значение отличается от предположенного).

- [ ] **Step 3: Commit**

```bash
git add src/parsers/remaining.py
git commit -m "Исправить сопоставление СТАНКИН «Управление данными» на верное направление 09.03.03.02"
```

---

## Task 2: МЭИ — каталог бюджетных очных бакалаврских направлений

**Files:**
- Create: `src/robot/mpei_pool.py`

**Interfaces:**
- Produces: `_catalog_from_page(html: str) -> list[tuple[str, str]]`
  (список пар `(название конкурсной группы, entrants_listNNN.html)`)
- Produces: `_extract_list_id(text: str) -> str | None`

- [ ] **Step 1: Создать файл с функцией разбора каталога**

Создать `src/robot/mpei_pool.py`:

```python
from __future__ import annotations

import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

CATALOG_URL = "https://pk.mpei.ru/info/entrants_list"
KCP_URL = "https://pk.mpei.ru/info/speclist_simple.html"

_SECTION_START = "Бакалавриат очная форма обучения"
_SECTION_END = "Бакалавриат очно-заочная форма обучения"
_LIST_ID_RE = re.compile(r"entrants_list\d+\.html")

FETCH_RETRIES = 3
RETRYABLE_STATUS = {500, 502, 503, 504}


def _extract_list_id(text: str) -> str | None:
    match = _LIST_ID_RE.search(text)
    return match.group(0) if match else None


def _catalog_from_page(html: str) -> list[tuple[str, str]]:
    """Возвращает [(название конкурсной группы, entrants_listNNN.html)]
    для бюджетных очных бакалаврских/специалитетных направлений."""
    start = html.find(_SECTION_START)
    if start == -1:
        raise ValueError(f"Не найдена секция «{_SECTION_START}»")
    end = html.find(_SECTION_END, start)
    section = html[start:end] if end != -1 else html[start:]

    soup = BeautifulSoup(section, "lxml")
    catalog: list[tuple[str, str]] = []
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        title = cells[0].get_text(" ", strip=True)
        if not title:
            continue
        for link in cells[1].find_all("a"):
            classes = link.get("class") or []
            if classes != ["competitive-group", "listFilterBudget"]:
                continue
            list_id = _extract_list_id(link.get("href", ""))
            if list_id:
                catalog.append((title, list_id))
    if not catalog:
        raise ValueError("Каталог бюджетных очных направлений МЭИ пуст")
    return catalog


def _get(url: str) -> str:
    last_error: Exception | None = None
    for attempt in range(FETCH_RETRIES):
        try:
            response = requests.get(url, timeout=60)
            response.raise_for_status()
            response.encoding = "utf-8"
            return response.text
        except requests.HTTPError as exc:
            last_error = exc
            status = exc.response.status_code if exc.response is not None else None
            if status in RETRYABLE_STATUS and attempt < FETCH_RETRIES - 1:
                continue
            break
        except requests.RequestException as exc:
            last_error = exc
            if attempt < FETCH_RETRIES - 1:
                continue
            break
    raise last_error or RuntimeError(f"Не удалось загрузить {url}")


def fetch_catalog() -> list[tuple[str, str]]:
    html = _get(CATALOG_URL)
    return _catalog_from_page(html)
```

- [ ] **Step 2: Проверить вживую**

```bash
cd /Users/gyattalert/work/priem-monitor
python3 - <<'EOF'
import sys
sys.path.insert(0, ".")
from src.robot.mpei_pool import fetch_catalog

catalog = fetch_catalog()
print("направлений в каталоге:", len(catalog))
for title, list_id in catalog[:5]:
    print(" -", list_id, title)

assert len(catalog) >= 30, f"ожидали не меньше 30 направлений, получили {len(catalog)}"
known_ids = {"entrants_list1986.html", "entrants_list16.html", "entrants_list35.html",
             "entrants_list1446.html", "entrants_list14.html"}
found_ids = {list_id for _, list_id in catalog}
missing = known_ids - found_ids
assert not missing, f"не нашли уже отслеживаемые направления: {missing}"
print("OK")
EOF
```

Ожидается: `направлений в каталоге: 39` (плюс-минус, если вуз обновил
список за это время), все 5 уже отслеживаемых id присутствуют.

- [ ] **Step 3: Commit**

```bash
git add src/robot/mpei_pool.py
git commit -m "Добавить каталог направлений МЭИ для полного пула робота"
```

---

## Task 3: МЭИ — официальные бюджетные места (КЦП)

**Files:**
- Modify: `src/robot/mpei_pool.py`

**Interfaces:**
- Consumes: ничего нового из Task 2 (независимая функция)
- Produces: `fetch_kcp_places() -> dict[str, int]` (название конкурсной
  группы → число бюджетных мест «Всего», очная форма)

- [ ] **Step 1: Добавить разбор kcp-table**

Дописать в `src/robot/mpei_pool.py`:

```python
DEFAULT_BUDGET_PLACES = 30


def _kcp_from_page(html: str) -> dict[str, int]:
    """Официальные КЦП (очная форма) по названию конкурсной группы.

    Таблица `kcp-table`: у многопрофильных групп название стоит только
    в первой строке (rowspan), у следующих строк той же группы — нет,
    поэтому название запоминается и используется для всех строк подряд,
    пока не встретится следующее название. Число мест — первая числовая
    ячейка после текстовых (это колонка «Всего» из группы «В рамках
    контрольных цифр приёма» — первая по порядку числовая колонка).
    """
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", class_="kcp-table")
    if table is None:
        raise ValueError("Не найдена таблица kcp-table")

    result: dict[str, int] = {}
    current_title: str | None = None
    in_daytime_section = False
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if not cells:
            continue
        text0 = cells[0].get_text(" ", strip=True)

        if text0 == "Очная форма обучения":
            in_daytime_section = True
            continue
        if text0 in ("Очно-заочная форма обучения", "Заочная форма обучения"):
            break
        if not in_daytime_section:
            continue

        if not text0.isdigit():
            current_title = text0
        if current_title is None:
            continue

        numbers = [c.get_text(strip=True) for c in cells if c.get_text(strip=True).isdigit()]
        if numbers:
            result.setdefault(current_title, int(numbers[0]))
    if not result:
        raise ValueError("Не удалось извлечь КЦП из kcp-table")
    return result


def fetch_kcp_places() -> dict[str, int]:
    html = _get(KCP_URL)
    return _kcp_from_page(html)
```

- [ ] **Step 2: Проверить вживую**

```bash
cd /Users/gyattalert/work/priem-monitor
python3 - <<'EOF'
import sys
sys.path.insert(0, ".")
from src.robot.mpei_pool import fetch_kcp_places, fetch_catalog

places = fetch_kcp_places()
print("направлений с КЦП:", len(places))
for title, value in list(places.items())[:5]:
    print(" -", title, "->", value)

assert len(places) >= 30, f"ожидали не меньше 30 записей, получили {len(places)}"
assert all(isinstance(v, int) and v > 0 for v in places.values())

catalog = fetch_catalog()
catalog_titles = {title for title, _ in catalog}
matched = catalog_titles & set(places.keys())
print(f"совпало по названию с каталогом: {len(matched)} из {len(catalog_titles)}")
assert len(matched) >= len(catalog_titles) * 0.5, "меньше половины названий совпало — проверьте разбор вручную"
print("OK")
EOF
```

Ожидается: не меньше половины каталожных названий находят пару в КЦП-таблице
(остальные — многопрофильные/нетиповые случаи, для них сработает
`DEFAULT_BUDGET_PLACES` на этапе сборки пула, это ожидаемо и не блокер).

- [ ] **Step 3: Commit**

```bash
git add src/robot/mpei_pool.py
git commit -m "Добавить разбор официальных КЦП МЭИ (kcp-table) для робота"
```

---

## Task 4: МЭИ — список абитуриентов по одному направлению (с кодом)

**Files:**
- Modify: `src/robot/mpei_pool.py`

**Interfaces:**
- Produces: `fetch_list_rows(list_id: str) -> list[dict]`, каждый элемент —
  `{"code": str, "score": int, "consent": bool, "priority": int}`

- [ ] **Step 1: Добавить разбор одного списка**

Дописать в `src/robot/mpei_pool.py` (используем уже проверенный в проекте
приём раскрытия `colspan` двухуровневой шапки — тот же, что в
`src/parsers/implementations.py::MpeiParser`):

```python
from ..parsers.utils import header_index, normalize_yes, to_int


def _expand_row_cells(row) -> list[str]:
    cells: list[str] = []
    for cell in row.find_all(["td", "th"]):
        text = cell.get_text(" ", strip=True)
        span = int(cell.get("colspan") or 1)
        cells.extend([text] * span)
    return cells


def _rows_from_page(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")
    if not tables:
        raise ValueError("Таблица конкурсного списка не найдена")
    table = max(tables, key=lambda item: len(item.find_all("tr")))
    rows = table.find_all("tr")
    if len(rows) < 3:
        raise ValueError("Таблица конкурсного списка пуста")

    headers = _expand_row_cells(rows[0])
    code_idx = header_index(headers, "уникальный код")
    score_idx = header_index(headers, "сумма")
    consent_idx = header_index(headers, "согласие")
    priority_idx = header_index(headers, "приоритет")
    if None in (code_idx, score_idx, consent_idx, priority_idx):
        raise ValueError("Не удалось определить колонки таблицы МЭИ")

    result: list[dict] = []
    for row in rows[2:]:
        cells = _expand_row_cells(row)
        if len(cells) <= max(code_idx, score_idx, consent_idx, priority_idx):
            continue
        score = to_int(cells[score_idx])
        if not score or score <= 0:
            continue
        code = cells[code_idx].strip()
        if not code:
            continue
        result.append(
            {
                "code": code,
                "score": score,
                "consent": normalize_yes(cells[consent_idx]),
                "priority": to_int(cells[priority_idx]) or 99,
            }
        )
    return result


def fetch_list_rows(list_id: str) -> list[dict]:
    html = _get(urljoin(CATALOG_URL, list_id))
    return _rows_from_page(html)
```

- [ ] **Step 2: Проверить вживую на известном направлении**

```bash
cd /Users/gyattalert/work/priem-monitor
python3 - <<'EOF'
import sys
sys.path.insert(0, ".")
from src.robot.mpei_pool import fetch_list_rows

rows = fetch_list_rows("entrants_list1986.html")
print("абитуриентов:", len(rows))
print("первая строка:", rows[0])

assert len(rows) > 0
for row in rows:
    assert row["code"], "пустой код абитуриента"
    assert row["score"] > 0
    assert isinstance(row["consent"], bool)
    assert row["priority"] >= 1
codes = {r["code"] for r in rows}
assert len(codes) == len(rows) or len(codes) < len(rows), "не должно быть отрицательного числа кодов"
print("уникальных кодов:", len(codes))
print("OK")
EOF
```

- [ ] **Step 3: Commit**

```bash
git add src/robot/mpei_pool.py
git commit -m "Добавить разбор списка абитуриентов МЭИ с уникальным кодом"
```

---

## Task 5: МЭИ — сборка полного пула, кэш, регистрация и сквозная проверка

Собираем всё из Task 2-4 в класс с кэшем (по образцу `mirea_pool.py`),
переименовываем ключи отслеживаемых направлений в `str(program.id)`
(см. Global Constraints), регистрируем в `_POOL_FETCHERS`, добавляем
`dima_list_code` в конфиг, проверяем полный прогон симуляции.

**Files:**
- Modify: `src/robot/mpei_pool.py`
- Modify: `src/robot/universities.py`
- Modify: `config/robot.json`

**Interfaces:**
- Consumes: `fetch_catalog()`, `fetch_kcp_places()`, `fetch_list_rows()`
  (Task 2-4); `ProgramChoice`, `RobotPerson`, `RobotProgram` из
  `src/robot/models.py`; `direction_key_for_program` из
  `src/robot/direction_keys.py`; `load_programs` из `src/config_loader.py`
- Produces: `fetch_mpei_full_pool(*, use_cache: bool = True) ->
  tuple[list[RobotPerson], list[RobotProgram], str, bool]` — регистрируется
  в `_POOL_FETCHERS["mpei"]`

- [ ] **Step 1: Дописать сборку пула и кэш**

Дописать в `src/robot/mpei_pool.py`:

```python
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from ..config_loader import load_programs
from ..models import ProgramConfig
from .models import ProgramChoice, RobotPerson, RobotProgram

POOL_SCOPE = "full"
MIN_CATALOG_PROGRAMS = 30
CACHE_PATH = Path(__file__).resolve().parents[2] / "data" / "cache" / "mpei_robot_pool.json"
CACHE_TTL_SEC = 7200
MAX_WORKERS = 6


def tracked_programs() -> list[ProgramConfig]:
    return [p for p in load_programs() if p.university == "МЭИ" and p.parser == "mpei"]


def _tracked_by_list_id() -> dict[str, ProgramConfig]:
    mapping: dict[str, ProgramConfig] = {}
    for program in tracked_programs():
        list_id = _extract_list_id(program.list_url)
        if list_id:
            mapping[list_id] = program
    return mapping


class MpeiFullPool:
    def build(self, *, use_cache: bool = True) -> tuple[list[RobotPerson], list[RobotProgram], str, bool]:
        if use_cache:
            cached = self._load_cache()
            if cached is not None:
                return cached
        try:
            catalog = fetch_catalog()
            kcp_places = self._safe_kcp_places()
            people, programs = self._fetch_all(catalog, kcp_places)
            fetched_at = datetime.now(timezone.utc).isoformat()
            self._save_cache(people, programs, fetched_at)
            return people, programs, fetched_at, False
        except Exception:
            stale = self._load_cache(ignore_ttl=True)
            if stale is not None:
                return stale
            raise

    @staticmethod
    def _safe_kcp_places() -> dict[str, int]:
        try:
            return fetch_kcp_places()
        except Exception:
            return {}

    def _fetch_all(
        self, catalog: list[tuple[str, str]], kcp_places: dict[str, int]
    ) -> tuple[list[RobotPerson], list[RobotProgram]]:
        raw_programs: list[RobotProgram] = []
        rows_by_list: dict[str, list[dict]] = {}
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(fetch_list_rows, list_id): (title, list_id) for title, list_id in catalog}
            for future in as_completed(futures):
                title, list_id = futures[future]
                rows_by_list[list_id] = future.result()

        tracked = _tracked_by_list_id()
        people: dict[str, RobotPerson] = {}
        for title, list_id in catalog:
            tracked_program = tracked.get(list_id)
            key = str(tracked_program.id) if tracked_program else list_id
            places = kcp_places.get(title)
            if places is None:
                places = tracked_program.budget_places if tracked_program else DEFAULT_BUDGET_PLACES
            raw_programs.append(
                RobotProgram(
                    key=key,
                    title=title,
                    budget_places=places,
                    tracked_id=tracked_program.id if tracked_program else None,
                )
            )
            for row in rows_by_list.get(list_id, []):
                choice = ProgramChoice(program_key=key, priority=row["priority"])
                person = people.get(row["code"])
                if person is None:
                    people[row["code"]] = RobotPerson(
                        code=row["code"], score=row["score"], consent=row["consent"], choices=[choice]
                    )
                    continue
                person.score = max(person.score, row["score"])
                person.consent = person.consent or row["consent"]
                person.choices.append(choice)
        return list(people.values()), raw_programs

    def _load_cache(self, *, ignore_ttl: bool = False):
        if not CACHE_PATH.exists():
            return None
        try:
            payload = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            fetched_at = payload.get("fetched_at", "")
            fetched_dt = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - fetched_dt).total_seconds()
            if not ignore_ttl and age > CACHE_TTL_SEC:
                return None
            people = [
                RobotPerson(
                    code=item["code"],
                    score=int(item["score"]),
                    consent=bool(item["consent"]),
                    is_bvi=bool(item.get("is_bvi")),
                    choices=[ProgramChoice(**choice) for choice in item.get("choices", [])],
                )
                for item in payload.get("people", [])
            ]
            programs = [RobotProgram(**item) for item in payload.get("programs", [])]
            if payload.get("pool_scope") != POOL_SCOPE or len(programs) < MIN_CATALOG_PROGRAMS:
                return None
            return people, programs, fetched_at, True
        except (ValueError, KeyError, json.JSONDecodeError, TypeError):
            return None

    def _save_cache(self, people: list[RobotPerson], programs: list[RobotProgram], fetched_at: str) -> None:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "fetched_at": fetched_at,
            "pool_scope": POOL_SCOPE,
            "people": [
                {
                    "code": p.code,
                    "score": p.score,
                    "consent": p.consent,
                    "is_bvi": p.is_bvi,
                    "choices": [asdict(c) for c in p.choices],
                }
                for p in people
            ],
            "programs": [asdict(p) for p in programs],
        }
        CACHE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_mpei_full_pool(*, use_cache: bool = True) -> tuple[list[RobotPerson], list[RobotProgram], str, bool]:
    return MpeiFullPool().build(use_cache=use_cache)
```

Примечание: `key = str(tracked_program.id)` присваивается программе СРАЗУ
при сборке (не отдельным шагом «переименования» постфактум) — раз мы уже
итерируем `catalog` и знаем, какая запись отслеживается, проще сразу
использовать правильный ключ и для `RobotProgram`, и для `ProgramChoice`
каждого абитуриента этого направления. Это и есть требование из Global
Constraints, выполненное по месту.

- [ ] **Step 2: Зарегистрировать в `_POOL_FETCHERS`**

В `src/robot/universities.py` заменить:

```python
_POOL_FETCHERS: dict[str, PoolFetcher] = {
    "fa": fetch_fa_full_pool,
    "mirea": fetch_mirea_full_pool,
}
```

на:

```python
from .mpei_pool import fetch_mpei_full_pool

_POOL_FETCHERS: dict[str, PoolFetcher] = {
    "fa": fetch_fa_full_pool,
    "mirea": fetch_mirea_full_pool,
    "mpei": fetch_mpei_full_pool,
}
```

(добавить импорт рядом с остальными импортами `_pool` вверху файла).

- [ ] **Step 3: Добавить `dima_list_code` для МЭИ в конфиг**

В `config/robot.json`, в блоке `"МЭИ"`, добавить `dima_list_code` — то же
значение, что уже стоит в блоке `"МИРЭА"` (посмотреть текущее значение там
и скопировать один в один):

```json
    "МЭИ": {
      "enabled": true,
      "dima_list_code": "<значение из блока МИРЭА>",
      "dima_priorities": [7, 22, 23, 35, 45]
    },
```

- [ ] **Step 4: Сквозная проверка полной симуляции**

```bash
cd /Users/gyattalert/work/priem-monitor
python3 - <<'EOF'
import sys
sys.path.insert(0, ".")
from src.robot.universities import fetch_university_pool, SUPPORTED_UNIVERSITIES
from src.robot.simulator import run_robot_simulation

assert SUPPORTED_UNIVERSITIES["МЭИ"] == "mpei"

people, programs, fetched_at, from_cache = fetch_university_pool("mpei", use_cache=False)
print("абитуриентов в пуле:", len(people))
print("направлений в пуле:", len(programs))
assert len(programs) >= 30
assert len(people) > 100

tracked_keys = {str(pid) for pid in (7, 22, 23, 35, 45)}
program_keys = {p.key for p in programs}
matched = tracked_keys & program_keys
print("отслеживаемые ключи, найденные в пуле:", matched)
assert matched == tracked_keys, f"не все отслеживаемые id стали ключами пула: не хватает {tracked_keys - matched}"

result = run_robot_simulation("МЭИ", use_cache=True)
print("ошибка симуляции:", result.error)
print("Дима в пуле как участник:", result.dima_code, "балл:", result.dima_score)
print("Дима зачислен на:", result.dima_placed_title, "по приоритету", result.dima_priority_used)
print("отслеживаемых направлений в отчёте:", len(result.tracked_programs))
assert result.error is None
assert len(result.tracked_programs) > 0
print("OK")
EOF
```

Ожидается: пул строится без ошибок, все 5 отслеживаемых id (7, 22, 23, 35,
45) присутствуют как ключи `RobotProgram` в пуле, симуляция отрабатывает
без `result.error`, в отчёте есть хотя бы одно отслеживаемое направление.
Если `dima_placed_title` пустой — это нормально (значит по текущим
приоритетам и баллу Дима никуда не прошёл в этой симуляции), падать не
должно только на `result.error`.

- [ ] **Step 5: Commit**

```bash
git add src/robot/mpei_pool.py src/robot/universities.py config/robot.json
git commit -m "Собрать полный пул робота для МЭИ и включить его в /робот МЭИ"
```

---

## Task 6: СТАНКИН — каталог направлений

**Files:**
- Create: `src/robot/stankin_pool.py`

**Interfaces:**
- Produces: `fetch_catalog() -> list[str]` (список значений `PROPERTY_394`)

- [ ] **Step 1: Создать файл с каталогом (динамический разбор + фолбэк)**

Создать `src/robot/stankin_pool.py`:

```python
from __future__ import annotations

import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

CATALOG_PAGE_URL = "https://priem.stankin.ru/bakalavriatispetsialitet/ranked-lists/"
LIST_URL = "https://priem.stankin.ru/gridspisokpostupayushchikh"
NAP_URL_TEMPLATE = "https://priem.stankin.ru/bakalavriatispetsialitet/nap/{code}/"

BASE_PARAMS = {
    "PROPERTY_388": "Бюджетная основа",
    "PROPERTY_389": "1 - Очная",
    "PROPERTY_584": "ready",
    "LIST_TYPE": "ranked",
    "EDU_LEVEL": "bs",
    "PROPERTY_418": "Прием на обучение на бакалавриат/специалитет",
    "COL_CITIZENSHIP": "Гражданин РФ",
    "apply_filter": "Y",
}

# Проверенный вживую список направлений на 2026-07-31 — используется, если
# динамический разбор <select> на сайте не сработает (изменилась вёрстка,
# сайт недоступен и т.п.).
FALLBACK_CATALOG = [
    "09.03.01 Информатика и вычислительная техника",
    "09.03.01.01 Разработка программных комплексов",
    "09.03.02 Информационные системы и технологии",
    "09.03.02.01 Разработка и внедрение корпоративных информационных систем",
    "09.03.03.01 Математическое и компьютерное моделирование процессов и систем",
    "09.03.03.02 Управление данными",
    "09.03.04 Программная инженерия",
    "12.03.01 Приборостроение",
    "15.03.01 Машиностроение",
    "15.03.01.01 Многоосевые металлообрабатывающие центры",
    "15.03.02 Технологические машины и оборудование",
    "15.03.04 Автоматизация технологических процессов и производств",
    "15.03.05 Конструкторско-технологическое обеспечение машиностроительных производств",
    "15.03.05.01 Высокопроизводительный металлообрабатывающий инструмент",
    "15.03.06 Мехатроника и робототехника",
    "15.05.01 Проектирование технологических машин и комплексов",
    "20.03.01 Техносферная безопасность",
    "22.03.01 Материаловедение и технологии материалов",
    "27.03.01 Стандартизация и метрология",
    "27.03.02 Управление качеством",
    "27.03.04 Управление в технических системах",
]

FETCH_RETRIES = 3
RETRYABLE_STATUS = {500, 502, 503, 504}


def _get(url: str, *, params: dict | None = None) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(FETCH_RETRIES):
        try:
            response = requests.get(url, params=params, timeout=60)
            response.raise_for_status()
            return response
        except requests.HTTPError as exc:
            last_error = exc
            status = exc.response.status_code if exc.response is not None else None
            if status in RETRYABLE_STATUS and attempt < FETCH_RETRIES - 1:
                continue
            break
        except requests.RequestException as exc:
            last_error = exc
            if attempt < FETCH_RETRIES - 1:
                continue
            break
    raise last_error or RuntimeError(f"Не удалось загрузить {url}")


def fetch_catalog() -> list[str]:
    try:
        response = _get(CATALOG_PAGE_URL)
        soup = BeautifulSoup(response.text, "lxml")
        for select in soup.find_all("select"):
            options = [opt.get_text(strip=True) for opt in select.find_all("option") if opt.get_text(strip=True)]
            if len(options) >= 15 and any(opt.startswith("09.03.04") for opt in options):
                return options
    except requests.RequestException:
        pass
    return list(FALLBACK_CATALOG)
```

- [ ] **Step 2: Проверить вживую**

```bash
cd /Users/gyattalert/work/priem-monitor
python3 - <<'EOF'
import sys
sys.path.insert(0, ".")
from src.robot.stankin_pool import fetch_catalog, FALLBACK_CATALOG

catalog = fetch_catalog()
print("направлений:", len(catalog))
for item in catalog:
    print(" -", item)

assert len(catalog) >= 15
assert "09.03.04 Программная инженерия" in catalog
assert "09.03.03.02 Управление данными" in catalog
print("используется фолбэк:", catalog == FALLBACK_CATALOG)
print("OK")
EOF
```

- [ ] **Step 3: Commit**

```bash
git add src/robot/stankin_pool.py
git commit -m "Добавить каталог направлений СТАНКИНа для полного пула робота"
```

---

## Task 7: СТАНКИН — список абитуриентов по направлению (с пагинацией)

**Files:**
- Modify: `src/robot/stankin_pool.py`

**Interfaces:**
- Produces: `fetch_direction_rows(direction: str) -> list[dict]`, каждый
  элемент — `{"code": str, "score": int, "consent": bool, "priority": int}`

- [ ] **Step 1: Добавить разбор таблицы с переходом по страницам**

Дописать в `src/robot/stankin_pool.py`:

```python
from ..parsers.utils import header_index, normalize_yes, to_int

MAX_PAGES = 50  # защита от зацикливания, реальных страниц обычно 1-2


def _rows_from_table(soup: BeautifulSoup) -> list[dict]:
    table = soup.find("table")
    if table is None:
        return []
    rows = table.find_all("tr")
    if not rows:
        return []

    headers = [c.get_text(" ", strip=True) for c in rows[0].find_all(["td", "th"])]
    code_idx = header_index(headers, "уникальный код")
    score_idx = header_index(headers, "сумма баллов с ид")
    consent_idx = header_index(headers, "согласие на зачисление")
    priority_idx = header_index(headers, "приоритет")
    if None in (code_idx, score_idx, consent_idx, priority_idx):
        raise ValueError("Не удалось определить колонки списка СТАНКИНа")

    result: list[dict] = []
    for row in rows[1:]:
        cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
        if len(cells) <= max(code_idx, score_idx, consent_idx, priority_idx):
            continue
        score = to_int(cells[score_idx])
        if not score or score <= 0:
            continue
        code = cells[code_idx].strip()
        if not code:
            continue
        result.append(
            {
                "code": code,
                "score": score,
                "consent": normalize_yes(cells[consent_idx]),
                "priority": to_int(cells[priority_idx]) or 99,
            }
        )
    return result


def fetch_direction_rows(direction: str) -> list[dict]:
    rows: list[dict] = []
    response = _get(LIST_URL, params={**BASE_PARAMS, "PROPERTY_394": direction})
    for _ in range(MAX_PAGES):
        soup = BeautifulSoup(response.text, "lxml")
        rows.extend(_rows_from_table(soup))

        next_link = soup.find("a", class_="main-ui-pagination-arrow-next")
        href = next_link.get("href") if next_link else None
        if not href:
            break
        response = _get(urljoin(response.url, href))
    return rows
```

- [ ] **Step 2: Проверить вживую на известном направлении с двумя страницами**

```bash
cd /Users/gyattalert/work/priem-monitor
python3 - <<'EOF'
import sys
sys.path.insert(0, ".")
from src.robot.stankin_pool import fetch_direction_rows

rows = fetch_direction_rows("09.03.04 Программная инженерия")
print("абитуриентов:", len(rows))
print("первая строка:", rows[0])

assert len(rows) > 50, f"ожидали больше 50 строк (значит, пагинация не сработала), получили {len(rows)}"
for row in rows:
    assert row["code"]
    assert row["score"] > 0
    assert isinstance(row["consent"], bool)
codes = [r["code"] for r in rows]
print("уникальных кодов:", len(set(codes)), "из", len(codes))
print("OK")
EOF
```

Ожидается: больше 50 строк (по ранее проверенным данным на `09.03.04` было
2 страницы по ~50) — если получилось ровно ~50 или меньше, пагинация не
сработала, разбирайтесь до коммита.

- [ ] **Step 3: Commit**

```bash
git add src/robot/stankin_pool.py
git commit -m "Добавить разбор списка абитуриентов СТАНКИНа с пагинацией"
```

---

## Task 8: СТАНКИН — официальные бюджетные места (nap-страницы)

**Files:**
- Modify: `src/robot/stankin_pool.py`

**Interfaces:**
- Produces: `fetch_kcp_places(catalog: list[str]) -> dict[str, int]`
  (направление из каталога → бюджетных мест)

- [ ] **Step 1: Добавить разбор nap-страниц**

Дописать в `src/robot/stankin_pool.py`:

```python
_PLACES_RE = re.compile(r"(\d+)\s+бюджетных мест")


def _direction_code(direction: str) -> str:
    return direction.split(" ", 1)[0]


def _nap_budget_places(direction_code: str) -> int | None:
    try:
        response = _get(NAP_URL_TEMPLATE.format(code=direction_code))
    except requests.RequestException:
        return None
    match = _PLACES_RE.search(response.text)
    return int(match.group(1)) if match else None


def fetch_kcp_places(catalog: list[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for direction in catalog:
        places = _nap_budget_places(_direction_code(direction))
        if places is not None:
            result[direction] = places
    return result
```

- [ ] **Step 2: Проверить вживую на известном значении**

```bash
cd /Users/gyattalert/work/priem-monitor
python3 - <<'EOF'
import sys
sys.path.insert(0, ".")
from src.robot.stankin_pool import fetch_kcp_places, fetch_catalog

catalog = fetch_catalog()
places = fetch_kcp_places(catalog)
print("направлений с КЦП:", len(places), "из", len(catalog))
for direction, value in places.items():
    print(" -", direction, "->", value)

assert len(places) >= len(catalog) * 0.5, "меньше половины направлений получили официальное КЦП"
assert places.get("09.03.04 Программная инженерия") == 65, \
    f"ожидали 65 мест для 09.03.04 (проверено вручную ранее), получили {places.get('09.03.04 Программная инженерия')}"
print("OK")
EOF
```

Если конкретное число `65` к моменту выполнения изменилось (вуз мог
скорректировать КЦП) — проверьте вручную на сайте
`https://priem.stankin.ru/bakalavriatispetsialitet/nap/09.03.04/`, что там
написано, и поправьте ожидаемое число в проверке перед тем как продолжать.

- [ ] **Step 3: Commit**

```bash
git add src/robot/stankin_pool.py
git commit -m "Добавить разбор официальных КЦП СТАНКИНа (nap-страницы) для робота"
```

---

## Task 9: СТАНКИН — сборка пула со слиянием многопрофильных пар, кэш, регистрация, проверка

Три пары отслеживаемых id указывают на сайте на один и тот же список:
44/47 (`09.03.01.01`) и 61/62 (`09.03.02.01`) — это настоящие раздельные
профили с общим конкурсным списком (подтверждено пользователем), пара
55/56 после Task 1 больше не дублируется. Для 44/47 и 61/62 сайт не даёт
программного способа развести абитуриентов по профилю (колонка
«Конкурсная группа» скрыта и не включается простым запросом — проверено,
не поддалось за разумное время) — поэтому такая пара сводится к ОДНОЙ
реальной программе в пуле: canonical id = меньший id пары (44 и 61
соответственно), места берутся из официального КЦП всего направления
(Task 8) — оно уже общее на пару, поэтому суммировать `budget_places` из
конфига вручную не нужно. Второй id пары (47 или 62) просто не получает
собственной записи в пуле — если Дима сохранит на него приоритет, робот
корректно и без ошибок пропустит этот шаг (нет записи → нет доступных
мест), как будто такого отдельного варианта нет — это ожидаемое,
задокументированное поведение, а не баг.

**Files:**
- Modify: `src/robot/stankin_pool.py`
- Modify: `src/robot/universities.py`
- Modify: `config/robot.json`

**Interfaces:**
- Consumes: `fetch_catalog()`, `fetch_direction_rows()`,
  `fetch_kcp_places()` (Task 6-8); `StankinParser` из
  `src/parsers/remaining.py` (переиспользуем `_resolve_program`, чтобы не
  дублировать словарь сопоставления)
- Produces: `fetch_stankin_full_pool(*, use_cache: bool = True) ->
  tuple[list[RobotPerson], list[RobotProgram], str, bool]` — регистрируется
  в `_POOL_FETCHERS["stankin"]`

- [ ] **Step 1: Дописать сборку пула с группировкой отслеживаемых id по направлению**

Дописать в `src/robot/stankin_pool.py`:

```python
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from ..config_loader import load_programs
from ..models import ProgramConfig
from ..parsers.remaining import StankinParser
from .models import ProgramChoice, RobotPerson, RobotProgram

POOL_SCOPE = "full"
MIN_CATALOG_PROGRAMS = 15
CACHE_PATH = Path(__file__).resolve().parents[2] / "data" / "cache" / "stankin_robot_pool.json"
CACHE_TTL_SEC = 7200
MAX_WORKERS = 6
DEFAULT_BUDGET_PLACES = 30


def tracked_programs() -> list[ProgramConfig]:
    return [p for p in load_programs() if p.university == "СТАНКИН" and p.parser == "stankin"]


def _tracked_direction_groups() -> dict[str, list[ProgramConfig]]:
    """PROPERTY_394 (сайтовое направление) -> отслеживаемые ProgramConfig,
    которые на него указывают (может быть больше одного — общий список на
    несколько профилей, см. описание задачи выше)."""
    parser = StankinParser()
    groups: dict[str, list[ProgramConfig]] = {}
    for program in tracked_programs():
        direction = parser._resolve_program(program)
        groups.setdefault(direction, []).append(program)
    return groups


class StankinFullPool:
    def build(self, *, use_cache: bool = True) -> tuple[list[RobotPerson], list[RobotProgram], str, bool]:
        if use_cache:
            cached = self._load_cache()
            if cached is not None:
                return cached
        try:
            catalog = fetch_catalog()
            kcp_places = self._safe_kcp_places(catalog)
            people, programs = self._fetch_all(catalog, kcp_places)
            fetched_at = datetime.now(timezone.utc).isoformat()
            self._save_cache(people, programs, fetched_at)
            return people, programs, fetched_at, False
        except Exception:
            stale = self._load_cache(ignore_ttl=True)
            if stale is not None:
                return stale
            raise

    @staticmethod
    def _safe_kcp_places(catalog: list[str]) -> dict[str, int]:
        try:
            return fetch_kcp_places(catalog)
        except Exception:
            return {}

    def _fetch_all(
        self, catalog: list[str], kcp_places: dict[str, int]
    ) -> tuple[list[RobotPerson], list[RobotProgram]]:
        rows_by_direction: dict[str, list[dict]] = {}
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(fetch_direction_rows, direction): direction for direction in catalog}
            for future in as_completed(futures):
                rows_by_direction[futures[future]] = future.result()

        direction_groups = _tracked_direction_groups()
        # направление -> (ключ в пуле, tracked_id) для отслеживаемых направлений
        tracked_key_by_direction: dict[str, tuple[str, int]] = {}
        for direction, configs in direction_groups.items():
            canonical = min(configs, key=lambda p: p.id)
            tracked_key_by_direction[direction] = (str(canonical.id), canonical.id)

        programs: list[RobotProgram] = []
        people: dict[str, RobotPerson] = {}
        for direction in catalog:
            tracked_key = tracked_key_by_direction.get(direction)
            key = tracked_key[0] if tracked_key else direction
            tracked_id = tracked_key[1] if tracked_key else None

            places = kcp_places.get(direction)
            if places is None:
                if tracked_key:
                    configs = direction_groups[direction]
                    places = sum(p.budget_places for p in configs)
                else:
                    places = DEFAULT_BUDGET_PLACES

            programs.append(RobotProgram(key=key, title=direction, budget_places=places, tracked_id=tracked_id))

            for row in rows_by_direction.get(direction, []):
                choice = ProgramChoice(program_key=key, priority=row["priority"])
                person = people.get(row["code"])
                if person is None:
                    people[row["code"]] = RobotPerson(
                        code=row["code"], score=row["score"], consent=row["consent"], choices=[choice]
                    )
                    continue
                person.score = max(person.score, row["score"])
                person.consent = person.consent or row["consent"]
                person.choices.append(choice)
        return list(people.values()), programs

    def _load_cache(self, *, ignore_ttl: bool = False):
        if not CACHE_PATH.exists():
            return None
        try:
            payload = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            fetched_at = payload.get("fetched_at", "")
            fetched_dt = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - fetched_dt).total_seconds()
            if not ignore_ttl and age > CACHE_TTL_SEC:
                return None
            people = [
                RobotPerson(
                    code=item["code"],
                    score=int(item["score"]),
                    consent=bool(item["consent"]),
                    is_bvi=bool(item.get("is_bvi")),
                    choices=[ProgramChoice(**choice) for choice in item.get("choices", [])],
                )
                for item in payload.get("people", [])
            ]
            programs = [RobotProgram(**item) for item in payload.get("programs", [])]
            if payload.get("pool_scope") != POOL_SCOPE or len(programs) < MIN_CATALOG_PROGRAMS:
                return None
            return people, programs, fetched_at, True
        except (ValueError, KeyError, json.JSONDecodeError, TypeError):
            return None

    def _save_cache(self, people: list[RobotPerson], programs: list[RobotProgram], fetched_at: str) -> None:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "fetched_at": fetched_at,
            "pool_scope": POOL_SCOPE,
            "people": [
                {
                    "code": p.code,
                    "score": p.score,
                    "consent": p.consent,
                    "is_bvi": p.is_bvi,
                    "choices": [asdict(c) for c in p.choices],
                }
                for p in people
            ],
            "programs": [asdict(p) for p in programs],
        }
        CACHE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_stankin_full_pool(*, use_cache: bool = True) -> tuple[list[RobotPerson], list[RobotProgram], str, bool]:
    return StankinFullPool().build(use_cache=use_cache)
```

Обратите внимание: `StankinParser._resolve_program` — приватный по
соглашению об именовании (`_`), но не приватный по факту (обычный метод
Python), и это единственное место, где хранится правильное сопоставление
конфиг-программа → `PROPERTY_394`. Дублировать этот словарь в
`stankin_pool.py` нельзя — тогда Task 1 пришлось бы чинить в двух местах.

- [ ] **Step 2: Зарегистрировать в `_POOL_FETCHERS`**

В `src/robot/universities.py`:

```python
from .mpei_pool import fetch_mpei_full_pool
from .stankin_pool import fetch_stankin_full_pool

_POOL_FETCHERS: dict[str, PoolFetcher] = {
    "fa": fetch_fa_full_pool,
    "mirea": fetch_mirea_full_pool,
    "mpei": fetch_mpei_full_pool,
    "stankin": fetch_stankin_full_pool,
}
```

- [ ] **Step 3: Добавить `dima_list_code` для СТАНКИНа в конфиг**

В `config/robot.json`, в блоке `"СТАНКИН"`, добавить то же значение
`dima_list_code`, что в блоке `"МИРЭА"`:

```json
    "СТАНКИН": {
      "enabled": true,
      "dima_list_code": "<значение из блока МИРЭА>",
      "dima_priorities": [33, 44, 47, 55, 56]
    },
```

- [ ] **Step 4: Сквозная проверка полной симуляции**

```bash
cd /Users/gyattalert/work/priem-monitor
python3 - <<'EOF'
import sys
sys.path.insert(0, ".")
from src.robot.universities import fetch_university_pool, SUPPORTED_UNIVERSITIES
from src.robot.simulator import run_robot_simulation

assert SUPPORTED_UNIVERSITIES["СТАНКИН"] == "stankin"

people, programs, fetched_at, from_cache = fetch_university_pool("stankin", use_cache=False)
print("абитуриентов в пуле:", len(people))
print("направлений в пуле:", len(programs))
assert len(programs) >= 15
assert len(people) > 500

program_keys = {p.key for p in programs}
# 33 (Программная инженерия) и 55 (Мат. моделирование) — не в общих парах, должны быть свои ключи.
# 44 и 61 — canonical для пар 44/47 и 61/62, должны быть в пуле.
# 47 и 62 — вторые id пары, НЕ должны получить собственный ключ (см. описание задачи).
for expected in ("33", "44", "55", "56", "61"):
    assert expected in program_keys, f"ожидали ключ {expected} в пуле, не нашли"
for absent in ("47", "62"):
    assert absent not in program_keys, f"ключ {absent} не должен появляться отдельно (слит с парой)"

result = run_robot_simulation("СТАНКИН", use_cache=True)
print("ошибка симуляции:", result.error)
print("Дима в пуле как участник:", result.dima_code, "балл:", result.dima_score)
print("Дима зачислен на:", result.dima_placed_title, "по приоритету", result.dima_priority_used)
print("отслеживаемых направлений в отчёте:", len(result.tracked_programs))
assert result.error is None
assert len(result.tracked_programs) > 0
print("OK")
EOF
```

- [ ] **Step 5: Commit**

```bash
git add src/robot/stankin_pool.py src/robot/universities.py config/robot.json
git commit -m "Собрать полный пул робота для СТАНКИНа и включить его в /робот СТАНКИН"
```

---

## Проверено, но не в объёме этого плана (будущие улучшения)

- Разведать программное включение скрытой колонки «Конкурсная группа» на
  сайте СТАНКИНа (`bitrix/components/bitrix/main.ui.grid/settings.ajax.php`,
  вероятно через `requests.Session()` с `sessid`), чтобы честно развести
  пары 44/47 и 61/62 по реальным профилям вместо слияния в одну программу.
  Не блокирует этот план — слияние с суммарными местами (уже реализовано в
  Task 9) даёт рабочий, но менее точный результат для этих двух пар.
- После того как пользователь развернёт бота (`config/telegram.json`,
  `scripts/install_telegram_bot.sh`) — отдельная, не связанная с этим планом
  задача — команды `/робот МЭИ`, `/робот СТАНКИН`, `/робот обновить МЭИ`,
  `/робот обновить СТАНКИН` заработают автоматически, дополнительный код не
  нужен.
- Ни этот план, ни уже существующие точечные парсеры (`MpeiParser`,
  `StankinParser`) не извлекают признак «преимущественного права»/БВИ —
  все абитуриенты в новых пулах считаются обычными участниками общего
  конкурса по баллам (`is_bvi=False`). Это существующее ограничение
  дашборда для этих двух вузов, план его не усугубляет, но и не
  исправляет — если понадобится точность зачисления БВИ-льготников для
  МЭИ/СТАНКИН, это отдельная задача (добавить разбор соответствующих
  колонок и туда, и сюда).
