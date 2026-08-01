# Починка парсера Финуниверситета (FA) — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `FaParser.fetch()` в `src/parsers/remaining.py` снова возвращает реальных абитуриентов по всем 4 отслеживаемым направлениям Финуниверситета (id 13, 42, 54, 78), устойчиво к следующей перестановке колонок на сайте и к единичным сетевым сбоям.

**Architecture:** Точечная правка одного класса `FaParser`. Извлечение полей строки таблицы переводится с позиционных индексов ячеек (`cells[3]`, `cells[9]`, ...) на поиск по атрибуту `data-label`, которым сайт сам подписывает каждую `<td>`. HTTP-запрос за страницей оборачивается в ретраи по паттерну, уже использующемуся в `src/robot/mpei_pool.py`/`src/robot/stankin_pool.py`.

**Tech Stack:** Python 3.12, `requests`, `beautifulsoup4` (`lxml` парсер) — без изменений в зависимостях.

## Global Constraints

- Задача в beads: `priem-monitor-ht3`. Отдельная связанная задача `priem-monitor-q1d` (фейковый `DEFAULT_BUDGET_PLACES=30` в пулах ФА/МЭИ/СТАНКИНа) — вне области этого плана, не трогать.
- Проектный принцип: если реальные данные получить не удалось — это должна быть видимая ошибка (`ProgramResult.error`), а не тихая подмена усреднённым/оценочным/нулевым значением.
- В проекте нет pytest для парсеров — проверка каждого шага вживую, реальным запросом к `fa.ru`, как и во всех предыдущих планах этого репозитория.
- Область изменений — только `FaParser` в `src/parsers/remaining.py`. Другие парсеры, `utils.py`, `service.py`, робот-пулы не менять.
- Коммиты — в стиле репозитория (короткое повелительное наклонение на русском, без префиксов `feat:`/`fix:`), без `Co-Authored-By`.

Спека: `docs/superpowers/specs/2026-08-01-fa-parser-fix-design.md`.

---

### Task 1: Ретраи HTTP-запроса и очистка параметров

**Files:**
- Modify: `src/parsers/remaining.py:22` (после строки `USER_AGENT = ...` — добавить константы)
- Modify: `src/parsers/remaining.py:277-338` (класс `FaParser`)

**Interfaces:**
- Produces: `FaParser._get(self, params: dict) -> str` — возвращает HTML тела ответа, кидает исключение (`requests.HTTPError`/`requests.RequestException`/их подкласс) после исчерпания попыток. Используется в Task 2 без изменений сигнатуры.

- [ ] **Step 1: Добавить константы ретраев рядом с `USER_AGENT`**

В `src/parsers/remaining.py` сразу после строки:

```python
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
```

добавить:

```python

FA_FETCH_RETRIES = 3
FA_RETRYABLE_STATUS = {500, 502, 503, 504}
```

- [ ] **Step 2: Заменить прямой `requests.get` на метод с ретраями, убрать `itype_list`**

Текущий класс `FaParser` (строки 277-338):

```python
class FaParser(BaseParser):
    name = "fa"

    def fetch(self, program: ProgramConfig) -> ProgramResult:
        fetched_at = datetime.now(timezone.utc).isoformat()
        try:
            keywords = program.filter_rules.get("program_keywords") or [program.program.split(",")[0].strip()]
            keyword = keywords[0]
            applicants: list[Applicant] = []
            page = 1
            total = None

            while True:
                params = {
                    "itype_list": "бкл",
                    "facultet": keyword,
                    "type_conkurs": "Общий конкурс",
                    "form_pay": "Бюджет",
                    "page": page,
                }
                html = requests.get(
                    "https://www.fa.ru/spiski/listabit.php",
                    params=params,
                    headers={"User-Agent": USER_AGENT},
                    timeout=60,
                ).text
                if total is None:
                    match = re.search(r"total:\s*(\d+)", html)
                    total = int(match.group(1)) if match else 0

                soup = BeautifulSoup(html, "lxml")
                page_rows = 0
                for row in soup.select("tbody tr"):
                    cells = [cell.get_text(" ", strip=True) for cell in row.find_all("td")]
                    if not cells or not cells[0].isdigit():
                        continue
                    page_rows += 1
                    program_text = cells[3].lower()
                    if keywords and not any(word.lower() in program_text for word in keywords):
                        continue
                    if "очно-заоч" in program_text or "dot" in program_text or "дот" in program_text:
                        continue
                    score = int(to_float(cells[9]) or 0)
                    if score <= 0:
                        continue
                    applicants.append(
                        Applicant(
                            score=score,
                            consent=normalize_yes(cells[17]),
                            priority=to_int(cells[4]) or 99,
                        )
                    )

                if page_rows == 0 or page * 20 >= total:
                    break
                page += 1

            if not applicants:
                raise ValueError("Список Финуниверситета пуст")
            return ProgramResult(program=program, applicants=applicants, fetched_at=fetched_at)
        except Exception as exc:  # noqa: BLE001
            return ProgramResult(program=program, applicants=[], fetched_at=fetched_at, error=str(exc))
```

Заменить целиком на (парсинг строк пока НЕ меняем — это Task 2, здесь только запрос):

```python
class FaParser(BaseParser):
    name = "fa"

    def _get(self, params: dict) -> str:
        last_error: Exception | None = None
        for attempt in range(FA_FETCH_RETRIES):
            try:
                response = requests.get(
                    "https://www.fa.ru/spiski/listabit.php",
                    params=params,
                    headers={"User-Agent": USER_AGENT},
                    timeout=60,
                )
                response.raise_for_status()
                return response.text
            except requests.HTTPError as exc:
                last_error = exc
                status = exc.response.status_code if exc.response is not None else None
                if status in FA_RETRYABLE_STATUS and attempt < FA_FETCH_RETRIES - 1:
                    continue
                break
            except requests.RequestException as exc:
                last_error = exc
                if attempt < FA_FETCH_RETRIES - 1:
                    continue
                break
        raise last_error or RuntimeError("Не удалось загрузить список Финуниверситета")

    def fetch(self, program: ProgramConfig) -> ProgramResult:
        fetched_at = datetime.now(timezone.utc).isoformat()
        try:
            keywords = program.filter_rules.get("program_keywords") or [program.program.split(",")[0].strip()]
            keyword = keywords[0]
            applicants: list[Applicant] = []
            page = 1
            total = None

            while True:
                params = {
                    "facultet": keyword,
                    "type_conkurs": "Общий конкурс",
                    "form_pay": "Бюджет",
                    "page": page,
                }
                html = self._get(params)
                if total is None:
                    match = re.search(r"total:\s*(\d+)", html)
                    total = int(match.group(1)) if match else 0

                soup = BeautifulSoup(html, "lxml")
                page_rows = 0
                for row in soup.select("tbody tr"):
                    cells = [cell.get_text(" ", strip=True) for cell in row.find_all("td")]
                    if not cells or not cells[0].isdigit():
                        continue
                    page_rows += 1
                    program_text = cells[3].lower()
                    if keywords and not any(word.lower() in program_text for word in keywords):
                        continue
                    if "очно-заоч" in program_text or "dot" in program_text or "дот" in program_text:
                        continue
                    score = int(to_float(cells[9]) or 0)
                    if score <= 0:
                        continue
                    applicants.append(
                        Applicant(
                            score=score,
                            consent=normalize_yes(cells[17]),
                            priority=to_int(cells[4]) or 99,
                        )
                    )

                if page_rows == 0 or page * 20 >= total:
                    break
                page += 1

            if not applicants:
                raise ValueError("Список Финуниверситета пуст")
            return ProgramResult(program=program, applicants=applicants, fetched_at=fetched_at)
        except Exception as exc:  # noqa: BLE001
            return ProgramResult(program=program, applicants=[], fetched_at=fetched_at, error=str(exc))
```

(Парсинг строк внутри `for row in soup.select(...)` дословно тот же, что и был — не переписывать заново, просто оставить как есть при замене блока.)

- [ ] **Step 3: Проверить вживую, что `_get` реально работает с ретраями и без `itype_list`**

Запустить:

```bash
cd /Users/gyattalert/work/priem-monitor
python3 -c "
from src.parsers.remaining import FaParser
import re

parser = FaParser()
html = parser._get({
    'facultet': 'Инженер-разработчик программного обеспечения',
    'type_conkurs': 'Общий конкурс',
    'form_pay': 'Бюджет',
    'page': 1,
})
print('length:', len(html))
print('has total:', bool(re.search(r'total:\s*(\d+)', html)))
"
```

Ожидается: `length` в районе 150000-200000, `has total: True`, без исключений. (Список абитуриентов на этом шаге всё ещё будет приходить пустым при вызове полного `fetch()` — это ожидаемо, чинится в Task 2.)

- [ ] **Step 4: Commit**

```bash
cd /Users/gyattalert/work/priem-monitor
git add src/parsers/remaining.py
git commit -m "Обернуть HTTP-запрос парсера Финуниверситета в ретраи, убрать неиспользуемый itype_list"
```

---

### Task 2: Извлечение полей по data-label вместо позиционных индексов

**Files:**
- Modify: `src/parsers/remaining.py` — класс `FaParser` (после Task 1: добавить `FIELD_LABELS`, заменить тело цикла `for row in soup.select("tbody tr"):` внутри `fetch()`)

**Interfaces:**
- Consumes: `FaParser._get(self, params: dict) -> str` из Task 1 (без изменений).
- Produces: не используется другими задачами — конечный потребитель это beads-задача `priem-monitor-ht3` (закрывается на последнем шаге).

- [ ] **Step 1: Добавить `FIELD_LABELS` и переписать извлечение полей строки**

В `FaParser`, сразу после `name = "fa"`, добавить:

```python
    FIELD_LABELS = {
        "program": "Конкурсная группа/образовательная программа",
        "priority": "Приоритет зачисления, указанный поступающим по данной конкурсной группе",
        "score": "Сумма конкурсных баллов",
        "consent": "Наличие согласия на зачисление",
    }
```

Внутри `fetch()`, блок:

```python
                soup = BeautifulSoup(html, "lxml")
                page_rows = 0
                for row in soup.select("tbody tr"):
                    cells = [cell.get_text(" ", strip=True) for cell in row.find_all("td")]
                    if not cells or not cells[0].isdigit():
                        continue
                    page_rows += 1
                    program_text = cells[3].lower()
                    if keywords and not any(word.lower() in program_text for word in keywords):
                        continue
                    if "очно-заоч" in program_text or "dot" in program_text or "дот" in program_text:
                        continue
                    score = int(to_float(cells[9]) or 0)
                    if score <= 0:
                        continue
                    applicants.append(
                        Applicant(
                            score=score,
                            consent=normalize_yes(cells[17]),
                            priority=to_int(cells[4]) or 99,
                        )
                    )
```

заменить на:

```python
                soup = BeautifulSoup(html, "lxml")
                page_rows = 0
                for row in soup.select("tbody tr"):
                    cells = row.find_all("td")
                    if not cells or not cells[0].get_text(strip=True).isdigit():
                        continue
                    page_rows += 1

                    fields = {(cell.get("data-label") or "").strip(): cell.get_text(" ", strip=True) for cell in cells}
                    for label in self.FIELD_LABELS.values():
                        if label not in fields:
                            raise ValueError(f"На сайте ФА не найдена колонка {label!r} — вёрстка снова изменилась")

                    program_text = fields[self.FIELD_LABELS["program"]].lower()
                    if keywords and not any(word.lower() in program_text for word in keywords):
                        continue
                    if "очно-заоч" in program_text or "dot" in program_text or "дот" in program_text:
                        continue
                    score = int(to_float(fields[self.FIELD_LABELS["score"]]) or 0)
                    if score <= 0:
                        continue
                    applicants.append(
                        Applicant(
                            score=score,
                            consent=normalize_yes(fields[self.FIELD_LABELS["consent"]]),
                            priority=to_int(fields[self.FIELD_LABELS["priority"]]) or 99,
                        )
                    )
```

Остальной код `fetch()` (пагинация, `if not applicants: raise ValueError(...)`, `except Exception`) не меняется.

- [ ] **Step 2: Проверить вживую на всех 4 направлениях ФА**

```bash
cd /Users/gyattalert/work/priem-monitor
python3 -c "
from src.config_loader import load_programs
from src.parsers.remaining import FaParser

programs = [p for p in load_programs() if p.parser == 'fa']
parser = FaParser()
for program in programs:
    result = parser.fetch(program)
    print(program.id, program.program[:50], '| applicants:', len(result.applicants), '| error:', result.error)
"
```

Ожидается: 4 строки (id 13, 42, 54, 78), у каждой `error: None` и `applicants` больше 0. Если у какого-то направления `error` не `None` — прочитать текст ошибки (при новом коде она будет содержать либо сетевую причину, либо конкретное имя ненайденной колонки) и разобраться перед тем, как считать шаг пройденным.

- [ ] **Step 3: Прогнать полное обновление и проверить дашборд-данные**

```bash
cd /Users/gyattalert/work/priem-monitor
python3 update.py
```

Ожидается в выводе: строки по направлениям ФА (Инженер-разработчик программного обеспечения / Разработка и внедрение информационно-аналитических систем / Прикладное машинное обучение / Прикладные информационные системы в экономике и финансах) отсутствуют в списке `failed` (в текущем выводе `update.py` печатает только проваленные направления построчно — если ФА туда не попал, значит фикс работает).

- [ ] **Step 4: Commit**

```bash
cd /Users/gyattalert/work/priem-monitor
git add src/parsers/remaining.py data/latest_results.json
git commit -m "Починить парсер Финуниверситета — извлекать поля по data-label вместо индексов ячеек"
```

- [ ] **Step 5: Закрыть задачу в beads**

```bash
~/.local/bin/bd close priem-monitor-ht3 --reason "Починено: извлечение по data-label + ретраи, проверено вживую по всем 4 направлениям"
```

## Self-Review

- **Spec coverage:** data-label извлечение — Task 2; явная ошибка на отсутствующую колонку — Task 2 Step 1; ретраи — Task 1; удаление `itype_list` — Task 1 Step 2; пагинация/фильтры не меняются — подтверждено (блоки вне цикла по `row` не тронуты); проверка вживую — Task 1 Step 3, Task 2 Step 2-3. Всё покрыто.
- **Placeholder scan:** плейсхолдеров/TODO нет, весь код — реальный, готовый к применению.
- **Type consistency:** `_get(self, params: dict) -> str` определён в Task 1, используется в Task 2 без изменений сигнатуры; `FIELD_LABELS` определяется и используется только в Task 2 — коллизий имён нет.
