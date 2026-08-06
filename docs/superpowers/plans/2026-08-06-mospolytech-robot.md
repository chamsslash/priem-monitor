# Робот Московского Политеха — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Спека:** `docs/superpowers/specs/2026-08-06-mospolytech-robot-design.md`

**Goal:** Пятый вуз в роботе-симуляторе по той же модели, что четыре готовых:
локальный каскад считает прогноз сам, независимый оракул сайта стоит рядом.

**Architecture:** Новый пул `src/robot/polytech_pool.py` по образцу
`stankin_pool.py`, но проще — источник отдаёт направление одним POST без
пагинации. Места берутся живьём из шапки списка. Регистрация вуза в
`universities.py` подключает его к воркеру и боту автоматически.

**Tech Stack:** Python 3, `requests`, `BeautifulSoup`/`lxml`,
`ThreadPoolExecutor`. Никакого asyncio — в проекте его нет нигде.

## Global Constraints

- Тестов в проекте нет. Приёмка каждой задачи — живой прогон конкретной команды
  с конкретным ожидаемым выводом.
- В сеть из продакшн-пути ходит только `RefreshWorker`. Пул сам по себе сетевых
  запросов вне `build()` не делает.
- `verify=False` запрещён. TLS чинится своим бандлом.
- Комментарии в коде — по-русски, как во всём проекте: объясняют ПОЧЕМУ, а не
  что делает строка.
- Коммиты конвенциональные, без `Co-Authored-By` (блокируется хуком).
- Числа мест и проходных берутся ТОЛЬКО из ответа
  `fio_list_curl.php`. Ручной досчёт квот запрещён.

## Опорные значения (сверено живьём 2026-08-06)

Эндпоинт:

```
POST https://mospolytech.ru/postupayushchim/priem-v-universitet/rating-abiturientov/fio_list_curl.php
Content-Type: application/x-www-form-urlencoded
select1=000000066_01&specCode=<spec>&eduForm=Очная&eduFin=Бюджетная основа&f=1
```

Каталог: `<select id="select2">` на странице
`https://mospolytech.ru/postupayushchim/priem-v-universitet/rating-abiturientov/`,
опции с `value="000000066_01"`, значение — атрибут `spec_code`. Их **65**.

Шапка списка — строка из 9 ячеек, первая матчится `^\d\d\.\d\d\.\d\d`:
`[код, название, КЦП, целевая, особая, отдельная, БВИ, остаток_общий_конкурс,
проходной_прошлого_года]`. Берём индекс **7**.

Строка абитуриента — 25 ячеек, первая — число. Индексы:
`2` уникальный код, `11` конкурсный балл, `13` согласие (`да`/пусто),
`14` приоритет, `16` высший проходной приоритет.

«Проходит сюда» = `cells[16]` непусто **и** `cells[16] == cells[14]`.

Контрольные числа по девяти отслеживаемым:

| specCode | остаток | людей | прошедших |
|---|---|---|---|
| `09.03.01_Системная и программная инженерия` | 36 | 4137 | 36 |
| `09.03.03_Большие и открытые данные` | 46 | 3654 | 46 |
| `09.03.03_Разработка и интеграция бизнес-приложений` | 45 | 3496 | 45 |
| `09.03.01_Разработка инженерного программного обеспечения` | 59 | 4644 | 59 |
| `09.03.01_Программирование электронных устройств и систем; Информационные системы умных пространств` | 59 | 3832 | 59 |
| `09.03.02_Интеллектуальные информационно-измерительные системы` | 27 | 2986 | 27 |
| `09.03.01_Интеллектуальные беспилотные системы` | 67 | 2269 | 67 |
| `01.03.02_Программирование и интеллектуальные системы управления транспортом` | 55 | 3201 | 55 |
| `09.03.01_Искусственный интеллект и машинное обучение` | 52 | 4296 | 52 |

Числа живые и меняются день ото дня. Инвариант, который обязан держаться
всегда: **прошедших == остаток** по каждому направлению.

---

### Задача 1: сеть Политеха — TLS, каталог, загрузка направления

**Files:**
- Create: `config/certs/globalsign-gcc-r3-dv-tls-ca-2020.pem`
- Create: `src/robot/polytech_pool.py`

**Interfaces:**
- Produces: `_ca_bundle() -> str`, `fetch_catalog() -> list[str]`,
  `fetch_direction_html(spec: str, *, session=None) -> str`,
  `CATALOG_PAGE_URL`, `LIST_URL`, `BASE_PARAMS`, `FALLBACK_CATALOG`

- [ ] **Шаг 1: положить промежуточный сертификат**

Скачать и сохранить в PEM:

```bash
curl -s http://secure.globalsign.com/cacert/gsgccr3dvtlsca2020.crt -o /tmp/gs.crt
openssl x509 -inform DER -in /tmp/gs.crt -out config/certs/globalsign-gcc-r3-dv-tls-ca-2020.pem
openssl x509 -in config/certs/globalsign-gcc-r3-dv-tls-ca-2020.pem -noout -subject -fingerprint -sha256
```

Ожидается ровно:

```
subject=C=BE, O=GlobalSign nv-sa, CN=GlobalSign GCC R3 DV TLS CA 2020
sha256 Fingerprint=76:25:38:43:95:09:C4:11:C4:37:D3:C5:67:56:3E:13:78:67:12:81:FC:4A:14:64:AD:D0:31:87:08:43:67:6E
```

Если отпечаток не совпал — **остановиться и доложить**, не подставлять другой
сертификат.

- [ ] **Шаг 2: `_ca_bundle()`**

Собирает `certifi.where()` + этот PEM в один файл во временном каталоге, один
раз за процесс (модульная переменная-кэш). Комментарий обязателен и должен
объяснять причину:

> Сервер mospolytech.ru не досылает промежуточный сертификат (`openssl s_client`
> → `Verify return code: 21`). curl берёт недостающее звено из системного
> хранилища, Python с certifi — нет. Поэтому подкладываем его сами.
> `verify=False` не годится: это источник официальных чисел, и подменить его
> смог бы кто угодно в канале.

- [ ] **Шаг 3: `fetch_catalog()`**

GET на `CATALOG_PAGE_URL`, найти `<select id="select2">`, собрать `spec_code`
опций с `value="000000066_01"`. При сетевой ошибке или пустом результате —
вернуть `FALLBACK_CATALOG` (65 проверенных значений, вписать константой), как
это сделано у СТАНКИНа.

- [ ] **Шаг 4: `fetch_direction_html(spec)`**

POST с `BASE_PARAMS` + `specCode`, `verify=_ca_bundle()`, таймаут 180,
ретраи на 500/502/503/504 по образцу `stankin_pool._get`.

Пагинации нет — цикла по страницам быть НЕ должно.

- [ ] **Шаг 5: приёмка**

```bash
python3 -c "
import sys; sys.path.insert(0,'.')
from src.robot.polytech_pool import fetch_catalog, fetch_direction_html
c = fetch_catalog(); print('каталог:', len(c))
h = fetch_direction_html('09.03.01_Искусственный интеллект и машинное обучение')
print('байт:', len(h), '| есть шапка:', 'Остаток' in h)
"
```

Ожидается: `каталог: 65`, байт > 5_000_000, `есть шапка: True`.

- [ ] **Шаг 6: коммит**

```bash
git add config/certs src/robot/polytech_pool.py
git commit -m "feat(robot): Политех — сеть, TLS-бандл и каталог направлений"
```

---

### Задача 2: разбор ответа и сборка пула

**Files:**
- Modify: `src/robot/polytech_pool.py`

**Interfaces:**
- Consumes: `fetch_catalog`, `fetch_direction_html` из задачи 1
- Produces: `fetch_polytech_full_pool(*, use_cache=True)`,
  `read_polytech_cached_pool()`, `CACHE_TTL_SEC = 7200`, `MAX_WORKERS = 6`,
  `tracked_programs()`

- [ ] **Шаг 1: `_parse_header(rows)`**

Ищет строку из 9 ячеек, первая матчится `^\d\d\.\d\d\.\d\d`. Возвращает
`(code, title, places)` где `places = int(cells[7])`.

Если такой строки нет — `ValueError` с внятным текстом. Молча подставлять число
нельзя: это ровно та болезнь, из-за которой места дважды протухали.

- [ ] **Шаг 2: `_parse_people(rows)`**

Строки из 25 ячеек с числом в первой. На выходе список словарей:
`code` (idx 2), `score` (idx 11), `consent` (idx 13 == `да`),
`priority` (idx 14 или 99), `top_passing` (idx 16 непусто и == idx 14).

Строки с нечисловым или нулевым баллом пропускаются, как в остальных пулах.

- [ ] **Шаг 3: `_passing_cutoff` / `_passing_count`**

Ровно та же семантика, что в `stankin_pool` (скопировать поведение, не
изобретать): cutoff — минимальный балл среди `top_passing is True`, `0` если
таких нет, `None` если колонки не было вовсе.

- [ ] **Шаг 4: `PolytechFullPool`**

`build()` — кэш → каталог → `ThreadPoolExecutor(max_workers=6)` по направлениям
→ склейка людей по коду → `RobotProgram` на направление. При исключении —
падать на устаревший кэш, как у СТАНКИНа.

`RobotProgram`: `key = str(tracked_id)` для отслеживаемых (см.
`direction_keys.direction_key_for_program`, там `mospolytech` уже есть), иначе
`spec_code`; `budget_places` из шапки; `seat_source="live"`;
`passing_cutoff`; `site_passing_count`. Поля `quota_shortfall` и
`vacant_places` НЕ заполняются — обоснование в спеке.

`MAX_FAILED_FRACTION = 0.5` — при массовом сбое `RuntimeError`, чтобы не
перезаписать кэш почти пустым датасетом.

- [ ] **Шаг 5: кэш**

`data/cache/polytech_robot_pool.json`, `POOL_SCOPE="full"`,
`MIN_CATALOG_PROGRAMS = 50`, `CACHE_TTL_SEC = 7200`. Формат и методы
`_load_cache`/`_save_cache` — как у СТАНКИНа.

- [ ] **Шаг 6: приёмка — главный инвариант**

```bash
python3 -c "
import sys; sys.path.insert(0,'.')
import time
from src.robot.polytech_pool import fetch_polytech_full_pool
t=time.time()
people, programs, at, cached = fetch_polytech_full_pool(use_cache=False)
print(f'{len(programs)} напр, {len(people)} чел, {time.time()-t:.0f} c')
bad = [p for p in programs if p.seat_source != 'live']
print('не live:', len(bad))
mism = [(p.title[:40], p.budget_places, p.site_passing_count)
        for p in programs if p.site_passing_count is not None
        and p.budget_places != p.site_passing_count]
print('прошедших != мест:', len(mism))
for m in mism[:5]: print('   ', m)
"
```

Ожидается: `65 напр`, ~25000 чел, время < 60 c, `не live: 0`.
`прошедших != мест` — в идеале 0. Расхождения возможны на непрофильных
направлениях, где идёт другая волна; если их больше 5 — **остановиться и
доложить**, не «поправлять» число.

Повторный вызов с `use_cache=True` обязан вернуть `cached=True` мгновенно.

- [ ] **Шаг 7: коммит**

```bash
git add src/robot/polytech_pool.py
git commit -m "feat(robot): Политех — разбор списков, места из шапки и сборка пула"
```

---

### Задача 3: регистрация пятого вуза

**Files:**
- Modify: `src/tracked_universities.py`
- Modify: `src/robot/universities.py`
- Modify: `src/robot/refresh_worker.py:20-22`
- Modify: `src/robot/verification.py` (`PLACEMENT_VERIFIED_UNIVERSITIES`)
- Modify: `config/programs.json`
- Modify: `config/robot.json`

**Interfaces:**
- Consumes: `fetch_polytech_full_pool`, `read_polytech_cached_pool`,
  `CACHE_TTL_SEC` из задачи 2

- [ ] **Шаг 1: `tracked_universities.py`**

Вернуть `"Московский политех"` в кортеж. Комментарий про SSL заменить на
короткую запись, ЧЕМ починено (промежуточный сертификат в `config/certs/`) —
чтобы через полгода никто не выключил вуз снова по той же причине.

- [ ] **Шаг 2: `universities.py`**

`SUPPORTED_UNIVERSITIES["Московский политех"] = "mospolytech"`, плюс записи в
`_POOL_FETCHERS`, `_CACHE_READERS`, `_CACHE_TTLS` и
`_EXPECTED_REFRESH_SEC["mospolytech"] = 20` с комментарием о замере.

Инвариант `SUPPORTED_UNIVERSITIES == TRACKED_UNIVERSITIES` после шага 1
сходится.

- [ ] **Шаг 3: `refresh_worker.py`**

`MAX_PARALLEL_REFRESH = 4` → `5`. Комментарий «Четыре вуза бьют по четырём
разным сайтам» переписать под пять и добавить, почему число привязано к
количеству вузов: иначе пятый встаёт в очередь за чужой сборкой и к его 13
секундам добавляются минуты ФА и СТАНКИНа.

- [ ] **Шаг 4: `verification.py`**

Добавить `"Московский политех"` в `PLACEMENT_VERIFIED_UNIVERSITIES` и строку в
комментарий над множеством: оракул — колонка «Высший проходной приоритет» в
ответе `fio_list_curl.php`, места — «Остаток на общий конкурс» из шапки того же
ответа, вердикт сайта о людях на вход каскада не идёт.

- [ ] **Шаг 5: конфиги**

`config/programs.json` — девяти программам Политеха проставить `budget_places`
= места общего конкурса (36/46/45/59/59/27/67/55/52 по таблице выше);
у `id26` убрать из `program` хвост «; Искусственный интеллект и машинное
обучение» (это отдельная группа `id76`).

`config/robot.json` — в `dima_priorities` Политеха поменять местами `63` и
`71`: настоящий порядок с сайта — `[40, 76, 26, 64, 57, 58, 63, 71, 65]` c
`71` на седьмом месте, `63` на восьмом, то есть
`[40, 76, 26, 64, 57, 58, 71, 63, 65]`.

- [ ] **Шаг 6: приёмка**

```bash
python3 -c "
import sys; sys.path.insert(0,'.')
from src.telegram_users import build_robot_settings
from src.robot.simulator import run_robot_simulation
from src.robot.format import format_robot_result
r = run_robot_simulation('Московский политех',
        settings=build_robot_settings('1824102','Московский политех'), stale_ok=True)
print('error:', r.error)
print(format_robot_result(r))
print('сверка:', r.verification.placement.status if r.verification and r.verification.placement else None)
print('места живьём у всех:', r.verification.all_seats_live if r.verification else None)
"
```

Ожидается: `error: None`; **9 приоритетов** в порядке
Большие и открытые данные → ИИ и МО → Системная и программная инженерия →
Интеллектуальные информационно-измерительные → Разработка и интеграция
бизнес-приложений → Разработка инженерного ПО → ПИСУТ → Программирование
электронных устройств → Интеллектуальные беспилотные;
`сверка: match` (или `boundary` при ничьей — тогда доложить, но не чинить);
`места живьём у всех: True`.

- [ ] **Шаг 7: приёмка воркера**

```bash
python3 -c "
import sys, time; sys.path.insert(0,'.')
from src.robot.refresh_worker import get_refresh_worker
w = get_refresh_worker()
done = []
t = time.time()
ok = w.request('Московский политех', on_done=lambda u,e: done.append((u,e)), force=True)
print('заявка принята за %.3f c (должно быть мгновенно):' % (time.time()-t), ok)
while not done: time.sleep(1)
print('готово за %.0f c, ошибка: %s' % (time.time()-t, done[0][1]))
"
```

Ожидается: заявка возвращается за доли секунды, сборка завершается без ошибки.

- [ ] **Шаг 8: коммит**

```bash
git add src/tracked_universities.py src/robot/universities.py \
        src/robot/refresh_worker.py src/robot/verification.py \
        config/programs.json config/robot.json
git commit -m "feat(robot): подключить Московский политех пятым вузом"
```
