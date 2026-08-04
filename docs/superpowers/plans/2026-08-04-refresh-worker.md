# Refresh-воркер: убрать сеть из цикла опроса Telegram — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Обработчик команды Telegram физически не может ходить в интернет — он читает кэш и отвечает за доли секунды, а все пересборки пулов уходят в единственный фоновый refresh-воркер.

**Architecture:** Три слоя с чёткой границей. Читатель кэша (`read_cached_pool`) отдаёт данные любого возраста и никогда не ходит в сеть. Refresh-воркер — единственное место в кодовой базе, вызывающее `fetch_university_pool(..., use_cache=False)`; держит single-flight по вузу, чтобы шедулер и команда не пересобирали один пул дважды. Обработчики и шедулер прогрева только читают кэш и кидают воркеру заявки.

**Tech Stack:** Python 3.14, stdlib `threading` / `concurrent.futures.ThreadPoolExecutor`, `requests`. Тестового фреймворка в репозитории нет — проверка живыми скриптами и запуском бота.

## Global Constraints

- Спека: `docs/superpowers/specs/2026-08-04-refresh-worker-design.md`. Задача beads: `priem-monitor-9tv`.
- Работаем в основном чекауте `/Users/gyattalert/work/priem-monitor` на ветке `main`.
- **Ведущий инвариант:** после реализации во всей кодовой базе остаётся ровно ОДНО место, вызывающее `fetch_university_pool(..., use_cache=False)` — метод `RefreshWorker._run`. Проверяется грепом.
- Цикл `get_updates()` в `main()` (`scripts/telegram_bot.py`) не должен блокироваться дольше, чем на чтение кэша.
- Контрольные счётчики абитуриентов после рефактора не меняются: СТАНКИН 8255, ФА ≈21637, МИРЭА ≈31617, МЭИ ≈19392.
- Комментарии и сообщения бота — на русском, как во всей кодовой базе.
- Коммиты — конвенциональные, БЕЗ строки `Co-Authored-By` (её блокирует git-хук).
- Ничего не пушим: пуш делает пользователь отдельным решением.

## Структура файлов

| Файл | Ответственность | Задача |
|---|---|---|
| `src/robot/fa_pool.py`, `mirea_pool.py`, `mpei_pool.py`, `stankin_pool.py` | +1 функция чтения кэша наружу (обёртка над своим приватным `_load_cache`) | 1 |
| `src/robot/universities.py` | Реестр читателей `_CACHE_READERS` + TTL, публичные `read_cached_pool` / `is_pool_stale` | 1 |
| `src/robot/simulator.py` | Режим `stale_ok` — брать пул из кэша вместо сети | 2 |
| `src/robot/refresh_worker.py` **(новый)** | Единственный владелец сетевых пересборок + single-flight | 3 |
| `scripts/telegram_bot.py` | Шедулер-продюсер заявок; обработчики читают кэш и шлют заявки | 4, 5 |
| `src/robot/format.py` | Пометка «обновляю в фоне» при протухшем кэше | 5 |

---

### Task 1: Чтение кэша без сети

**Files:**
- Modify: `src/robot/fa_pool.py` (добавить функцию в конец файла, после `fetch_fa_full_pool` на строке 311)
- Modify: `src/robot/mirea_pool.py` (после `fetch_mirea_full_pool`, строка 301)
- Modify: `src/robot/mpei_pool.py` (после `fetch_mpei_full_pool`, строка 347)
- Modify: `src/robot/stankin_pool.py` (после `fetch_stankin_full_pool`, строка 519)
- Modify: `src/robot/universities.py` (шапка импортов, строки 1-32)
- Verify: `/private/tmp/claude-501/verify_task1.py` (временный скрипт, не коммитим)

**Interfaces:**
- Consumes: существующий приватный метод `_load_cache(self, *, ignore_ttl: bool = False)`, одинаковый во всех 4 пулах; возвращает `(people, programs, fetched_at, True)` или `None`. Существующая константа `CACHE_TTL_SEC = 7200` в каждом пуле.
- Produces:
  - `read_fa_cached_pool()`, `read_mirea_cached_pool()`, `read_mpei_cached_pool()`, `read_stankin_cached_pool()` — каждая `() -> tuple[list[RobotPerson], list[RobotProgram], str, bool] | None`
  - `universities.read_cached_pool(parser_name: str) -> tuple[list[RobotPerson], list[RobotProgram], str, bool] | None`
  - `universities.is_pool_stale(parser_name: str, fetched_at: str | None) -> bool`

- [ ] **Step 1: Добавить читатель в `fa_pool.py`**

В конец файла, сразу после `fetch_fa_full_pool`:

```python
def read_fa_cached_pool() -> tuple[list[RobotPerson], list[RobotProgram], str, bool] | None:
    """Отдаёт кэш ФА ЛЮБОГО возраста и никогда не ходит в сеть.

    Нужна обработчикам команд: они обязаны отвечать мгновенно, поэтому берут
    что есть на диске, а протухание — повод отправить заявку refresh-воркеру,
    а не качать прямо здесь. None — кэша нет вовсе (или он несовместим).
    """
    return FaFullPool()._load_cache(ignore_ttl=True)
```

- [ ] **Step 2: Добавить такие же читатели в остальные 3 пула**

`src/robot/mirea_pool.py`:

```python
def read_mirea_cached_pool() -> tuple[list[RobotPerson], list[RobotProgram], str, bool] | None:
    """Отдаёт кэш МИРЭА ЛЮБОГО возраста и никогда не ходит в сеть. См. read_fa_cached_pool."""
    return MireaFullPool()._load_cache(ignore_ttl=True)
```

`src/robot/mpei_pool.py`:

```python
def read_mpei_cached_pool() -> tuple[list[RobotPerson], list[RobotProgram], str, bool] | None:
    """Отдаёт кэш МЭИ ЛЮБОГО возраста и никогда не ходит в сеть. См. read_fa_cached_pool."""
    return MpeiFullPool()._load_cache(ignore_ttl=True)
```

`src/robot/stankin_pool.py`:

```python
def read_stankin_cached_pool() -> tuple[list[RobotPerson], list[RobotProgram], str, bool] | None:
    """Отдаёт кэш СТАНКИН ЛЮБОГО возраста и никогда не ходит в сеть. См. read_fa_cached_pool."""
    return StankinFullPool()._load_cache(ignore_ttl=True)
```

- [ ] **Step 3: Свести читатели и TTL в `universities.py`**

Заменить блок импортов (строки 1-11) на:

```python
from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime, timezone

from ..tracked_universities import TRACKED_UNIVERSITIES
from .fa_pool import CACHE_TTL_SEC as FA_CACHE_TTL_SEC
from .fa_pool import fetch_fa_full_pool, read_fa_cached_pool
from .mirea_pool import CACHE_TTL_SEC as MIREA_CACHE_TTL_SEC
from .mirea_pool import fetch_mirea_full_pool, read_mirea_cached_pool
from .models import RobotPerson, RobotProgram
from .mpei_pool import CACHE_TTL_SEC as MPEI_CACHE_TTL_SEC
from .mpei_pool import fetch_mpei_full_pool, read_mpei_cached_pool
from .stankin_pool import CACHE_TTL_SEC as STANKIN_CACHE_TTL_SEC
from .stankin_pool import fetch_stankin_full_pool, read_stankin_cached_pool

CachedPool = tuple[list[RobotPerson], list[RobotProgram], str, bool]
PoolFetcher = Callable[..., CachedPool]
CacheReader = Callable[[], CachedPool | None]
```

- [ ] **Step 4: Добавить реестры и публичные функции**

Сразу после существующего словаря `_POOL_FETCHERS` (строка 27-32) и функции `fetch_university_pool`:

```python
_CACHE_READERS: dict[str, CacheReader] = {
    "fa": read_fa_cached_pool,
    "mirea": read_mirea_cached_pool,
    "mpei": read_mpei_cached_pool,
    "stankin": read_stankin_cached_pool,
}

# TTL берём из самих пулов, а не дублируем число: если пул поменяет свой срок
# жизни кэша, «протухание» здесь поедет за ним автоматически.
_CACHE_TTLS: dict[str, int] = {
    "fa": FA_CACHE_TTL_SEC,
    "mirea": MIREA_CACHE_TTL_SEC,
    "mpei": MPEI_CACHE_TTL_SEC,
    "stankin": STANKIN_CACHE_TTL_SEC,
}

if set(_CACHE_READERS) != set(_POOL_FETCHERS) or set(_CACHE_TTLS) != set(_POOL_FETCHERS):
    raise RuntimeError("_CACHE_READERS/_CACHE_TTLS должны покрывать те же парсеры, что и _POOL_FETCHERS")


def read_cached_pool(parser_name: str) -> CachedPool | None:
    """Кэш вуза любого возраста, без единого сетевого запроса.

    Точка входа для обработчиков команд. None — кэша нет (первый запуск либо
    формат кэша устарел), тогда вызывающий обязан честно сказать «данные ещё
    собираются», а не пытаться собрать их сам.
    """
    reader = _CACHE_READERS.get(parser_name)
    if reader is None:
        raise ValueError(f"Читатель кэша для parser={parser_name} не зарегистрирован")
    return reader()


def is_pool_stale(parser_name: str, fetched_at: str | None) -> bool:
    """Старше ли кэш своего TTL. Нечитаемая/пустая метка времени = считаем протухшим."""
    if not fetched_at:
        return True
    ttl = _CACHE_TTLS.get(parser_name)
    if ttl is None:
        return True
    try:
        fetched_dt = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    return (datetime.now(timezone.utc) - fetched_dt).total_seconds() > ttl
```

- [ ] **Step 5: Проверить вживую — читает кэш и не ходит в сеть**

Создать `/private/tmp/claude-501/verify_task1.py`:

```python
import socket
import sys

sys.path.insert(0, "/Users/gyattalert/work/priem-monitor")

from src.robot.universities import SUPPORTED_UNIVERSITIES, is_pool_stale, read_cached_pool

# Глушим сеть: любой сетевой вызов внутри чтения кэша должен упасть с ошибкой.
def _no_network(*args, **kwargs):
    raise AssertionError("read_cached_pool полез в сеть — это баг")

socket.socket.connect = _no_network

for university, parser in sorted(SUPPORTED_UNIVERSITIES.items()):
    cached = read_cached_pool(parser)
    if cached is None:
        print(f"{university:24} кэша нет")
        continue
    people, programs, fetched_at, from_cache = cached
    stale = is_pool_stale(parser, fetched_at)
    print(f"{university:24} людей={len(people):6} направлений={len(programs):4} "
          f"протух={stale} from_cache={from_cache}")

print("OK: сеть не понадобилась")
```

Run: `cd /Users/gyattalert/work/priem-monitor && python3 /private/tmp/claude-501/verify_task1.py`

Expected: четыре строки со счётчиками (СТАНКИН 8255, ФА ≈21637, МИРЭА ≈31617, МЭИ ≈19392), затем `OK: сеть не понадобилась`. Никаких `AssertionError`.

- [ ] **Step 6: Проверить, что пустой кэш даёт None, а не падение**

Run:

```bash
cd /Users/gyattalert/work/priem-monitor && \
mv data/cache/stankin_robot_pool.json /private/tmp/claude-501/stankin_backup.json && \
python3 -c "
import sys; sys.path.insert(0,'.')
from src.robot.universities import read_cached_pool, is_pool_stale
print('нет кэша ->', read_cached_pool('stankin'))
print('протух при None ->', is_pool_stale('stankin', None))
" && \
mv /private/tmp/claude-501/stankin_backup.json data/cache/stankin_robot_pool.json && \
echo "кэш СТАНКИН возвращён на место"
```

Expected:
```
нет кэша -> None
протух при None -> True
кэш СТАНКИН возвращён на место
```

- [ ] **Step 7: Коммит**

```bash
cd /Users/gyattalert/work/priem-monitor
git add src/robot/fa_pool.py src/robot/mirea_pool.py src/robot/mpei_pool.py \
        src/robot/stankin_pool.py src/robot/universities.py
git commit -m "feat(robot): чтение кэша пулов без выхода в сеть (read_cached_pool)"
```

---

### Task 2: Режим `stale_ok` в симуляторе

**Files:**
- Modify: `src/robot/simulator.py` (импорт на строке 18; сигнатура `run_robot_simulation` строки 397-402; получение пула строки 444-448)
- Verify: `/private/tmp/claude-501/verify_task2.py` (временный, не коммитим)

**Interfaces:**
- Consumes: `universities.read_cached_pool(parser_name)`, `universities.is_pool_stale(parser_name, fetched_at)` из Task 1.
- Produces: `run_robot_simulation(university, settings=None, *, use_cache=True, stale_ok=False, priority_ids=None) -> RobotSimulationResult`. При `stale_ok=True` функция гарантированно не делает сетевых запросов. Если кэша нет — возвращает результат с `error = "Данные ещё собираются, вернусь через пару минут"`.

- [ ] **Step 1: Расширить импорт**

Строка 18 сейчас:

```python
from .universities import SUPPORTED_UNIVERSITIES, fetch_university_pool
```

Заменить на:

```python
from .universities import SUPPORTED_UNIVERSITIES, fetch_university_pool, read_cached_pool
```

- [ ] **Step 2: Добавить параметр `stale_ok` в сигнатуру**

Строки 397-402 сейчас:

```python
def run_robot_simulation(
    university: str,
    settings: RobotSettings | None = None,
    *,
    use_cache: bool = True,
    priority_ids: list[int] | None = None,
) -> RobotSimulationResult:
```

Заменить на:

```python
def run_robot_simulation(
    university: str,
    settings: RobotSettings | None = None,
    *,
    use_cache: bool = True,
    stale_ok: bool = False,
    priority_ids: list[int] | None = None,
) -> RobotSimulationResult:
    """stale_ok=True — брать пул ТОЛЬКО из кэша, любого возраста, и никогда не
    ходить в сеть. Режим для обработчиков команд Telegram: они крутятся в том же
    потоке, что и опрос getUpdates, поэтому сетевая пересборка там подвесила бы
    бота для всех чатов сразу. Пересборку заказывает refresh_worker."""
```

- [ ] **Step 3: Развилка получения пула**

Строки 444-448 сейчас:

```python
    try:
        people, programs, fetched_at, from_cache = fetch_university_pool(parser_name, use_cache=use_cache)
    except Exception as exc:  # noqa: BLE001
        empty.error = str(exc)
        return empty
```

Заменить на:

```python
    try:
        if stale_ok:
            cached = read_cached_pool(parser_name)
            if cached is None:
                empty.error = "Данные ещё собираются, вернусь через пару минут"
                return empty
            people, programs, fetched_at, from_cache = cached
        else:
            people, programs, fetched_at, from_cache = fetch_university_pool(parser_name, use_cache=use_cache)
    except Exception as exc:  # noqa: BLE001
        empty.error = str(exc)
        return empty
```

- [ ] **Step 4: Проверить — симуляция без сети и её скорость**

Создать `/private/tmp/claude-501/verify_task2.py`:

```python
import logging
import socket
import sys
import time

sys.path.insert(0, "/Users/gyattalert/work/priem-monitor")
logging.disable(logging.CRITICAL)

from src.robot.config import load_robot_config
from src.robot.simulator import run_robot_simulation
from src.robot.universities import robot_ready_universities

def _no_network(*args, **kwargs):
    raise AssertionError("stale_ok=True полез в сеть — это баг")

socket.socket.connect = _no_network

settings = load_robot_config()
started = time.time()
for university in sorted(robot_ready_universities()):
    result = run_robot_simulation(university, settings=settings, stale_ok=True)
    status = result.error or f"людей={result.total_people}"
    print(f"{university:24} {status}")
print(f"ИТОГО без сети: {time.time() - started:.2f}с")
```

Run: `cd /Users/gyattalert/work/priem-monitor && python3 /private/tmp/claude-501/verify_task2.py`

Expected: четыре строки с `людей=…` (СТАНКИН 8255, ФА ≈21637, МИРЭА ≈31617, МЭИ ≈19392), итог **менее 2 секунд**, ни одного `AssertionError`.

- [ ] **Step 5: Проверить сообщение при отсутствии кэша**

Run:

```bash
cd /Users/gyattalert/work/priem-monitor && \
mv data/cache/stankin_robot_pool.json /private/tmp/claude-501/stankin_backup.json && \
python3 -c "
import sys, logging; sys.path.insert(0,'.'); logging.disable(logging.CRITICAL)
from src.robot.simulator import run_robot_simulation
print(repr(run_robot_simulation('СТАНКИН', stale_ok=True).error))
" && \
mv /private/tmp/claude-501/stankin_backup.json data/cache/stankin_robot_pool.json && \
echo "кэш СТАНКИН возвращён на место"
```

Expected:
```
'Данные ещё собираются, вернусь через пару минут'
кэш СТАНКИН возвращён на место
```

- [ ] **Step 6: Коммит**

```bash
cd /Users/gyattalert/work/priem-monitor
git add src/robot/simulator.py
git commit -m "feat(robot): режим stale_ok — симуляция строго из кэша, без сети"
```

---

### Task 3: Refresh-воркер с single-flight

**Files:**
- Create: `src/robot/refresh_worker.py`
- Verify: `/private/tmp/claude-501/verify_task3.py` (временный, не коммитим)

**Interfaces:**
- Consumes: `universities.SUPPORTED_UNIVERSITIES`, `universities.fetch_university_pool`, `universities.read_cached_pool`, `universities.is_pool_stale`.
- Produces:
  - `DoneCallback = Callable[[str, Exception | None], None]` — колбэк `(university, error)`, `error=None` означает успех.
  - `class RefreshWorker` с методами `request(university: str, *, on_done: DoneCallback | None = None, force: bool = False) -> bool` и `shutdown() -> None`.
  - `get_refresh_worker() -> RefreshWorker` — общий на процесс экземпляр.

- [ ] **Step 1: Создать модуль**

Создать `src/robot/refresh_worker.py`:

```python
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from .universities import (
    SUPPORTED_UNIVERSITIES,
    fetch_university_pool,
    is_pool_stale,
    read_cached_pool,
)

logger = logging.getLogger(__name__)

# (university, error). error=None — пересборка удалась.
DoneCallback = Callable[[str, Exception | None], None]

# Четыре вуза бьют по четырём разным сайтам, поэтому греются параллельно:
# полный прогрев занимает время самого медленного, а не сумму.
MAX_PARALLEL_REFRESH = 4


class RefreshWorker:
    """Единственное место в кодовой базе, которое пересобирает пулы из сети.

    Ни обработчики команд, ни шедулер прогрева не качают сами — они шлют сюда
    заявку и сразу возвращаются. Так цикл getUpdates никогда не блокируется.

    Single-flight: пока вуз собирается, повторная заявка НЕ запускает вторую
    сборку, а подвешивает свой колбэк к идущей. Без этого шедулер и команда
    пользователя дублировали бы работу и удваивали нагрузку на сайт вуза.
    """

    def __init__(self, max_workers: int = MAX_PARALLEL_REFRESH) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="refresh")
        self._lock = threading.Lock()
        self._in_flight: dict[str, list[DoneCallback]] = {}

    def request(
        self,
        university: str,
        *,
        on_done: DoneCallback | None = None,
        force: bool = False,
    ) -> bool:
        """Заказать пересборку вуза. Возвращается немедленно, не блокирует.

        force=False — если кэш ещё свеж, пересборка не нужна и не запускается
        (так шедулер не перекачивает недавно обновлённое). force=True — качаем
        всегда: это явное «/робот обновить», где пользователь просит свежее.

        True  — запустили новую сборку,
        False — либо кэш уже свеж, либо подхватили идущую сборку.
        """
        parser_name = SUPPORTED_UNIVERSITIES.get(university)
        if parser_name is None:
            raise ValueError(f"Робот не поддерживает вуз «{university}»")

        if not force:
            cached = read_cached_pool(parser_name)
            if cached is not None and not is_pool_stale(parser_name, cached[2]):
                logger.info("Кэш %s ещё свеж — пересборка не нужна", university)
                if on_done is not None:
                    on_done(university, None)
                return False

        with self._lock:
            waiters = self._in_flight.get(university)
            if waiters is not None:
                if on_done is not None:
                    waiters.append(on_done)
                logger.info("Пересборка %s уже идёт — заявка подхвачена", university)
                return False
            self._in_flight[university] = [on_done] if on_done is not None else []

        self._executor.submit(self._run, university, parser_name)
        return True

    def _run(self, university: str, parser_name: str) -> None:
        error: Exception | None = None
        try:
            people, programs, _fetched_at, _from_cache = fetch_university_pool(
                parser_name, use_cache=False
            )
            logger.info(
                "Пересобран %s: направлений %d, абитуриентов %d",
                university,
                len(programs),
                len(people),
            )
        except Exception as exc:  # noqa: BLE001
            error = exc
            logger.warning("Пересборка %s не удалась: %s", university, exc)
        finally:
            # Снимаем метку ВСЕГДА, иначе одна упавшая сборка навсегда
            # заблокировала бы повторные попытки для этого вуза.
            with self._lock:
                waiters = self._in_flight.pop(university, [])

        # Колбэки — вне лока: чужой код (отправка в Telegram) не должен
        # держать мьютекс и тормозить заявки по другим вузам.
        for callback in waiters:
            try:
                callback(university, error)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Колбэк пересборки %s упал: %s", university, exc)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False)


_worker: RefreshWorker | None = None
_worker_lock = threading.Lock()


def get_refresh_worker() -> RefreshWorker:
    """Общий воркер процесса. Именно общий: single-flight имеет смысл, только
    если шедулер прогрева и обработчики команд шлют заявки в ОДИН экземпляр."""
    global _worker
    with _worker_lock:
        if _worker is None:
            _worker = RefreshWorker()
        return _worker
```

- [ ] **Step 2: Проверить single-flight на подменённой сборке**

Создать `/private/tmp/claude-501/verify_task3.py`:

```python
import logging
import sys
import threading
import time

sys.path.insert(0, "/Users/gyattalert/work/priem-monitor")
logging.disable(logging.CRITICAL)

import src.robot.refresh_worker as rw

calls: list[str] = []
calls_lock = threading.Lock()

def fake_fetch(parser_name, *, use_cache=True):
    with calls_lock:
        calls.append(parser_name)
    time.sleep(1.5)  # изображаем долгую пересборку
    return ([], [], "2026-08-04T00:00:00+00:00", False)

rw.fetch_university_pool = fake_fetch

worker = rw.RefreshWorker()
notified: list[tuple[str, object]] = []

# Три заявки на ОДИН вуз подряд, force=True (мимо проверки свежести).
started = [worker.request("СТАНКИН", on_done=lambda u, e: notified.append((u, e)), force=True)
           for _ in range(3)]

print("вернули True (новая сборка):", started)
time.sleep(3)

print("реальных вызовов сборки:", len(calls))
print("колбэков вызвано:", len(notified))
assert started == [True, False, False], "single-flight не сработал"
assert len(calls) == 1, f"ожидали 1 сборку, получили {len(calls)}"
assert len(notified) == 3, f"ожидали 3 колбэка, получили {len(notified)}"

# Разные вузы не должны блокировать друг друга.
calls.clear()
for university in ("СТАНКИН", "МЭИ", "МИРЭА"):
    worker.request(university, force=True)
time.sleep(3)
print("разных вузов собрано параллельно:", len(calls))
assert len(calls) == 3, f"ожидали 3 параллельных сборки, получили {len(calls)}"

# Упавшая сборка обязана снять метку in-flight.
def failing_fetch(parser_name, *, use_cache=True):
    raise RuntimeError("сайт лёг")

rw.fetch_university_pool = failing_fetch
errors: list[object] = []
worker.request("СТАНКИН", on_done=lambda u, e: errors.append(e), force=True)
time.sleep(1)
assert isinstance(errors[0], RuntimeError), f"ошибка не доехала до колбэка: {errors}"
assert worker.request("СТАНКИН", force=True) is True, "после падения вуз остался заблокирован"

worker.shutdown()
print("OK: single-flight, параллельность и снятие метки после падения работают")
```

Run: `cd /Users/gyattalert/work/priem-monitor && python3 /private/tmp/claude-501/verify_task3.py`

Expected:
```
вернули True (новая сборка): [True, False, False]
реальных вызовов сборки: 1
колбэков вызвано: 3
разных вузов собрано параллельно: 3
OK: single-flight, параллельность и снятие метки после падения работают
```

- [ ] **Step 3: Проверить, что свежий кэш не пересобирается**

Run:

```bash
cd /Users/gyattalert/work/priem-monitor && python3 -c "
import sys, logging; sys.path.insert(0,'.'); logging.disable(logging.CRITICAL)
import src.robot.refresh_worker as rw

def boom(*a, **k):
    raise AssertionError('полез пересобирать свежий кэш')

rw.fetch_university_pool = boom
worker = rw.RefreshWorker()
# Кэши сейчас свежие -> force=False обязан отказаться от сборки.
print('запустил сборку?', worker.request('СТАНКИН'))
worker.shutdown()
print('OK: свежий кэш не трогали')
"
```

Expected:
```
запустил сборку? False
OK: свежий кэш не трогали
```

- [ ] **Step 4: Проверить ведущий инвариант — сеть осталась в одном месте**

Run:

```bash
cd /Users/gyattalert/work/priem-monitor && \
grep -rn "use_cache=False" --include="*.py" src/ scripts/
```

Expected: ровно одна строка — в `src/robot/refresh_worker.py`, внутри `_run`. Если находится что-то ещё (кроме этой строки), инвариант нарушен и задача не выполнена.

- [ ] **Step 5: Коммит**

```bash
cd /Users/gyattalert/work/priem-monitor
git add src/robot/refresh_worker.py
git commit -m "feat(robot): refresh-воркер — единственный владелец сетевых пересборок, single-flight"
```

---

### Task 4: Шедулер прогрева становится продюсером заявок

**Files:**
- Modify: `scripts/telegram_bot.py` (константа `ROBOT_REFRESH_INTERVAL_SEC` строка 614; функция `_prewarm_robot_pools_once` строки 617-653; функция `_robot_pool_refresh_loop` строки 656-662)

**Interfaces:**
- Consumes: `refresh_worker.get_refresh_worker()` из Task 3; существующая `universities.robot_ready_universities()`.
- Produces: `_prewarm_robot_pools_once()` возвращается немедленно (не ждёт сборок). `ROBOT_REFRESH_INTERVAL_SEC = 5400`.

- [ ] **Step 1: Уменьшить интервал ниже TTL**

Строки 612-614 сейчас:

```python
# Период фонового прогрева робот-пулов. Совпадает с TTL кэша (~2 часа): после сна
# кэш протухает и на следующем витке пересобирается, оставаясь всегда свежим.
ROBOT_REFRESH_INTERVAL_SEC = 7200
```

Заменить на:

```python
# Период фонового прогрева робот-пулов. СТРОГО МЕНЬШЕ TTL кэша (7200с): при
# равенстве кэш протухал ровно к моменту очередного прогрева, и команда,
# попавшая в это окно, уходила пересобирать пул синхронно. 90 минут дают запас
# в полчаса, за который прогрев успевает обновить даже самый медленный вуз.
ROBOT_REFRESH_INTERVAL_SEC = 5400
```

- [ ] **Step 2: Переписать прогрев в продюсер заявок**

Строки 617-653 (вся функция `_prewarm_robot_pools_once`, вместе с вложенной `_warm` и блоком `ThreadPoolExecutor`) заменить на:

```python
def _prewarm_robot_pools_once() -> None:
    """Отправляет заявки на прогрев всех пулов и СРАЗУ возвращается.

    Сам ничего не качает: пересборкой владеет refresh_worker, он же держит
    single-flight — поэтому прогрев и команда пользователя, попавшие на один
    вуз, больше не дублируют работу. Свежие кэши воркер пропускает сам
    (force=False), так что перезапуск бота не перекачивает недавно собранное.
    """
    from src.robot.refresh_worker import get_refresh_worker
    from src.robot.universities import robot_ready_universities

    worker = get_refresh_worker()
    for university in robot_ready_universities():
        try:
            worker.request(university)
        except ValueError as exc:
            logger.warning("Заявка на прогрев %s отклонена: %s", university, exc)
    logger.info("Заявки на прогрев отправлены воркеру")
```

- [ ] **Step 3: Обновить комментарий цикла**

Строки 656-662 сейчас:

```python
def _robot_pool_refresh_loop() -> None:
    """Прогрев при старте и далее каждые ~2ч. После сна кэш протухает (age > TTL),
    поэтому use_cache=True на следующем витке его пересобирает — кэш держится свежим,
    а недавно использованные командой пулы зря не перекраулятся."""
    while True:
        _prewarm_robot_pools_once()
        time.sleep(ROBOT_REFRESH_INTERVAL_SEC)
```

Заменить на:

```python
def _robot_pool_refresh_loop() -> None:
    """Шлёт заявки на прогрев при старте и далее каждые 90 минут.

    Виток стоит доли секунды (заявки уходят в воркер и обрабатываются им
    параллельно), поэтому сон почти точно равен заданному интервалу."""
    while True:
        _prewarm_robot_pools_once()
        time.sleep(ROBOT_REFRESH_INTERVAL_SEC)
```

- [ ] **Step 4: Убрать импорт, если он осиротел**

Проверить, используется ли ещё `as_completed` (он был нужен старому прогреву):

Run: `cd /Users/gyattalert/work/priem-monitor && grep -n "as_completed\|ThreadPoolExecutor" scripts/telegram_bot.py`

Если `as_completed` больше не встречается в теле файла (кроме строки импорта 8), поправить импорт на строке 8:

```python
from concurrent.futures import ThreadPoolExecutor
```

Если и `ThreadPoolExecutor` больше не используется (это станет так после Task 5), строку импорта удалить целиком. На этом шаге — только `as_completed`; `ThreadPoolExecutor` ещё нужен обработчику `/робот обновить` до Task 5.

- [ ] **Step 5: Проверить, что прогрев возвращается мгновенно**

Run:

```bash
cd /Users/gyattalert/work/priem-monitor && python3 -c "
import sys, time, logging; sys.path.insert(0,'.')
logging.basicConfig(level=logging.INFO, format='%(message)s')
sys.argv = ['x']
import importlib.util
spec = importlib.util.spec_from_file_location('tb', 'scripts/telegram_bot.py')
tb = importlib.util.module_from_spec(spec); spec.loader.exec_module(tb)
t0 = time.time()
tb._prewarm_robot_pools_once()
print(f'ПРОГРЕВ ВЕРНУЛСЯ ЗА {time.time()-t0:.2f}с')
print('интервал:', tb.ROBOT_REFRESH_INTERVAL_SEC)
"
```

Expected: строки «Кэш … ещё свеж — пересборка не нужна» по каждому вузу, затем `ПРОГРЕВ ВЕРНУЛСЯ ЗА 0.xx с` (доли секунды, не минуты) и `интервал: 5400`.

- [ ] **Step 6: Коммит**

```bash
cd /Users/gyattalert/work/priem-monitor
git add scripts/telegram_bot.py
git commit -m "refactor(bot): шедулер прогрева шлёт заявки воркеру, интервал 90 мин ниже TTL"
```

---

### Task 5: Обработчики читают кэш и заказывают догрев

**Files:**
- Modify: `scripts/telegram_bot.py` — `/статус` (строки 381-395), `/робот обновить` (строки 407-457), `/робот <вуз>` (строки 469-485), `/конкуренты` (строка 546)
- Modify: `src/robot/format.py` — `_data_header` (строки 30-37)
- Modify: `scripts/telegram_bot.py` — `_format_multi_status` (строки 80-101)

**Interfaces:**
- Consumes: `run_robot_simulation(..., stale_ok=True)` из Task 2; `get_refresh_worker()` и `RefreshWorker.request(university, *, on_done, force)` из Task 3; `is_pool_stale(parser_name, fetched_at)` и `read_cached_pool(parser_name)` из Task 1; существующая `format.format_robot_cache_refresh(university, *, fetched_at, people_count, programs_count)`.
- Produces: ни одна команда бота больше не вызывает сетевую сборку синхронно.

- [ ] **Step 1: Пометка о протухших данных в заголовке**

`src/robot/format.py`, строки 30-37 сейчас:

```python
def _data_header(result: RobotSimulationResult) -> str:
    title = f"🤖 Симуляция робота — {result.university}"
    fetched = _format_fetched_at(result.fetched_at)
    if not fetched:
        return title
    if result.from_cache:
        return f"{title} (кэш от {fetched})"
    return f"{title} (данные от {fetched})"
```

Заменить на:

```python
def _data_header(result: RobotSimulationResult) -> str:
    title = f"🤖 Симуляция робота — {result.university}"
    fetched = _format_fetched_at(result.fetched_at)
    if not fetched:
        return title
    if result.from_cache:
        # Протухший кэш всё равно показываем (иначе бот молчал бы минутами),
        # но честно говорим, что цифры старые и свежие уже едут.
        from .universities import SUPPORTED_UNIVERSITIES, is_pool_stale

        parser_name = SUPPORTED_UNIVERSITIES.get(result.university)
        if parser_name is not None and is_pool_stale(parser_name, result.fetched_at):
            return f"{title} (кэш от {fetched} · обновляю в фоне)"
        return f"{title} (кэш от {fetched})"
    return f"{title} (данные от {fetched})"
```

Импорт внутри функции, а не в шапке, — намеренно: `universities.py` тянет за собой все четыре пула, а `format.py` импортируется в местах, где эта цепочка не нужна.

- [ ] **Step 2: `/статус` — кэш + заявка на догрев**

`scripts/telegram_bot.py`, строки 381-395 сейчас:

```python
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

Заменить на:

```python
    if command == "/статус":
        from src.robot.priorities import get_saved_priority_ids
        from src.robot.refresh_worker import get_refresh_worker
        from src.robot.simulator import run_robot_simulation
        from src.robot.universities import SUPPORTED_UNIVERSITIES, is_pool_stale, robot_ready_universities
        from src.telegram_users import build_robot_settings, get_user_code, robot_config_path

        code = get_user_code(chat_id)
        worker = get_refresh_worker()
        results = []
        stale_any = False
        for university in sorted(robot_ready_universities()):
            settings = build_robot_settings(code, university)
            priority_ids = get_saved_priority_ids(university, path=robot_config_path(chat_id))
            # stale_ok=True: читаем кэш и отвечаем мгновенно. Сетевая пересборка
            # здесь подвесила бы цикл getUpdates для всех чатов сразу.
            result = run_robot_simulation(university, settings=settings, stale_ok=True, priority_ids=priority_ids)
            results.append((university, result))
            if is_pool_stale(SUPPORTED_UNIVERSITIES[university], result.fetched_at):
                stale_any = True
                worker.request(university)
        send_message(config.bot_token, chat_id, _format_multi_status(code, results, stale=stale_any), reply_to=message_id)
        return
```

- [ ] **Step 3: Показать в `/статус`, что данные догреваются**

`scripts/telegram_bot.py`, строка 80 сейчас:

```python
def _format_multi_status(code: str, results: list) -> str:
```

Заменить на:

```python
def _format_multi_status(code: str, results: list, *, stale: bool = False) -> str:
```

И перед финальным `return "\n".join(lines)` (сейчас строки 99-101):

```python
    lines.append("")
    lines.append("Подробности по одному вузу: /робот <вуз>")
    return "\n".join(lines)
```

вставить строку про догрев:

```python
    if stale:
        lines.append("")
        lines.append("⏳ Часть данных устарела — обновляю в фоне, повторите через пару минут.")
    lines.append("")
    lines.append("Подробности по одному вузу: /робот <вуз>")
    return "\n".join(lines)
```

- [ ] **Step 4: `/робот обновить` — мгновенный ack и результат колбэком**

`scripts/telegram_bot.py`, строки 434-457 (вложенная `_refresh_one`, блок `ThreadPoolExecutor`, финальный `send_message` и `return`) заменить на:

```python
                # Пересборку ведёт воркер; здесь только ack, чтобы цикл опроса
                # не стоял 1–7 минут. Готовый результат прилетит колбэком.
                from src.robot.refresh_worker import get_refresh_worker
                from src.robot.universities import read_cached_pool

                def _notify(university: str, error: Exception | None) -> None:
                    if error is not None:
                        text = f"⚠️ {university}: не удалось обновить — {error}"
                    else:
                        cached = read_cached_pool(SUPPORTED_UNIVERSITIES[university])
                        if cached is None:
                            text = f"⚠️ {university}: обновление прошло, но кэш не читается"
                        else:
                            people, programs, fetched_at, _from_cache = cached
                            text = format_robot_cache_refresh(
                                university,
                                fetched_at=fetched_at,
                                people_count=len(people),
                                programs_count=len(programs),
                            )
                    try:
                        send_message(config.bot_token, chat_id, text)
                    except TelegramAPIError as exc:
                        logger.error("Не отправить итог обновления %s: %s", university, exc)

                worker = get_refresh_worker()
                for university in universities:
                    worker.request(university, on_done=_notify, force=True)
                return
```

Также заменить два ack-сообщения выше (строки 419-432), чтобы они не обещали ожидания:

```python
                if len(universities) > 1:
                    send_message(
                        config.bot_token,
                        chat_id,
                        "Обновляю кэш робота в фоне — пришлю результат по каждому вузу.",
                        reply_to=message_id,
                    )
                else:
                    send_message(
                        config.bot_token,
                        chat_id,
                        f"Обновляю кэш робота для {universities[0]} в фоне — пришлю результат, "
                        "как будет готово. Остальные команды работают как обычно.",
                        reply_to=message_id,
                    )
```

- [ ] **Step 5: `/робот <вуз>` — мгновенный ответ из кэша**

`scripts/telegram_bot.py`, строки 476-485 сейчас:

```python
            send_message(
                config.bot_token,
                chat_id,
                f"Запускаю симуляцию робота для {university}…\nЗагружаю списки, это может занять 1–3 минуты.",
                reply_to=message_id,
            )
            settings = build_robot_settings(code, university)
            priority_ids = get_saved_priority_ids(university, path=robot_config_path(chat_id))
            result = run_robot_simulation(university, settings=settings, use_cache=True, priority_ids=priority_ids)
            send_long_message(config.bot_token, chat_id, format_robot_result(result), reply_to=message_id)
```

Заменить на (промежуточное «загружаю списки» больше не нужно — ответ мгновенный):

```python
            from src.robot.refresh_worker import get_refresh_worker
            from src.robot.universities import is_pool_stale

            settings = build_robot_settings(code, university)
            priority_ids = get_saved_priority_ids(university, path=robot_config_path(chat_id))
            result = run_robot_simulation(university, settings=settings, stale_ok=True, priority_ids=priority_ids)
            send_long_message(config.bot_token, chat_id, format_robot_result(result), reply_to=message_id)
            if is_pool_stale(SUPPORTED_UNIVERSITIES[university], result.fetched_at):
                get_refresh_worker().request(university)
```

- [ ] **Step 6: `/конкуренты` — тот же режим**

`scripts/telegram_bot.py`, строка 546 сейчас:

```python
            result = run_robot_simulation(university, settings=settings, use_cache=True, priority_ids=priority_ids)
```

Заменить на:

```python
            result = run_robot_simulation(university, settings=settings, stale_ok=True, priority_ids=priority_ids)
```

- [ ] **Step 7: Убрать осиротевший импорт `ThreadPoolExecutor`**

Run: `cd /Users/gyattalert/work/priem-monitor && grep -n "ThreadPoolExecutor\|as_completed" scripts/telegram_bot.py`

Ожидается: только строка импорта 8. Тогда удалить её целиком:

```python
from concurrent.futures import ThreadPoolExecutor, as_completed
```

- [ ] **Step 8: Проверить, что сеть ушла из обработчиков**

Run:

```bash
cd /Users/gyattalert/work/priem-monitor && \
echo "--- use_cache=False должен быть только в воркере ---" && \
grep -rn "use_cache=False" --include="*.py" src/ scripts/ && \
echo "--- в обработчиках не должно остаться use_cache=True ---" && \
grep -n "run_robot_simulation(.*use_cache" scripts/telegram_bot.py || echo "чисто: обработчики на stale_ok"
```

Expected: единственная строка `use_cache=False` — в `src/robot/refresh_worker.py`; и `чисто: обработчики на stale_ok`.

- [ ] **Step 9: Живой прогон бота — главная проверка**

```bash
cd /Users/gyattalert/work/priem-monitor && \
: > logs/telegram_bot.err.log && \
nohup python3 scripts/telegram_bot.py >> logs/telegram_bot.out.log 2>> logs/telegram_bot.err.log & \
sleep 5 && tail -5 logs/telegram_bot.log && cat logs/telegram_bot.err.log
```

Затем вручную в Telegram проверить пять сценариев из спеки:

1. `/статус` отвечает **менее чем за секунду** (а не молчит минутами).
2. Отправить `/робот обновить`, сразу следом `/статус` — второй отвечает мгновенно, не дожидаясь первого.
3. По готовности `/робот обновить` присылает результат отдельным сообщением.
4. В логах при столкновении прогрева и команды — строка «Пересборка … уже идёт — заявка подхвачена», и ровно одна сборка вуза.
5. Проверить счётчики: `grep "Пересобран" logs/telegram_bot.log` — СТАНКИН 8255, ФА ≈21637, МИРЭА ≈31617, МЭИ ≈19392.

Остановить бота после проверки:

```bash
pkill -f "python3 scripts/telegram_bot.py" && echo "бот остановлен"
```

- [ ] **Step 10: Коммит**

```bash
cd /Users/gyattalert/work/priem-monitor
git add scripts/telegram_bot.py src/robot/format.py
git commit -m "feat(bot): обработчики отвечают из кэша и заказывают догрев у воркера"
```

---

## Проверка плана против спеки (self-review)

| Требование спеки | Задача |
|---|---|
| `read_cached_pool` + `_CACHE_READERS` поверх `_load_cache(ignore_ttl=True)` | 1 |
| `is_pool_stale` с порогом `CACHE_TTL_SEC` | 1 |
| `run_robot_simulation(..., stale_ok=False)`, правка в `simulator.py:445` | 2 |
| «Данные ещё собираются» при отсутствии кэша | 2 |
| `refresh_worker.py`: `ThreadPoolExecutor(4)`, `_in_flight` под `Lock`, снятие в `finally` | 3 |
| `request()` маппит имя вуза в parser через `SUPPORTED_UNIVERSITIES` | 3 |
| Колбэки вызываются вне лока | 3 |
| Воркер не импортирует ничего из Telegram | 3 |
| Шедулер — продюсер заявок; интервал 7200 → 5400 | 4 |
| `/статус`, `/робот <вуз>`, `/конкуренты` на `stale_ok=True` | 5 |
| Пометка «данные от HH:MM» при протухании | 5 (Step 1, 3) |
| `/робот обновить`: мгновенный ack + результат колбэком | 5 (Step 4) |
| Ошибка сборки → «⚠️ не удалось обновить …», кэш не тронут | 3 (`_run`), 5 (`_notify`) |
| Инвариант «сеть только в воркере» | 3 (Step 4), 5 (Step 8) |
| Контрольные счётчики абитуриентов | 1 (Step 5), 5 (Step 9) |

Пробелов не найдено; плейсхолдеров («TBD», «добавить обработку ошибок») в плане нет; имена и сигнатуры (`read_cached_pool`, `is_pool_stale`, `request(university, *, on_done, force)`, `stale_ok`) согласованы между задачами.