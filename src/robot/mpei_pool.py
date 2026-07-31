from __future__ import annotations

import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ..config_loader import load_programs
from ..models import ProgramConfig
from ..parsers.utils import header_index, normalize_yes, to_int
from .models import ProgramChoice, RobotPerson, RobotProgram

CATALOG_URL = "https://pk.mpei.ru/info/entrants_list"
KCP_URL = "https://pk.mpei.ru/info/speclist_simple.html"

_SECTION_START = "Бакалавриат очная форма обучения"
_SECTION_END = "Бакалавриат очно-заочная форма обучения"
_LIST_ID_RE = re.compile(r"entrants_list\d+\.html")

FETCH_RETRIES = 3
RETRYABLE_STATUS = {500, 502, 503, 504}

POOL_SCOPE = "full"
MIN_CATALOG_PROGRAMS = 30
CACHE_PATH = Path(__file__).resolve().parents[2] / "data" / "cache" / "mpei_robot_pool.json"
CACHE_TTL_SEC = 7200
MAX_WORKERS = 6
# Доля упавших списков, после которой сбой считается массовым/системным
# (троттлинг, авария сайта), а не единичным сетевым сбоем одного списка —
# при превышении _fetch_all бросает исключение, чтобы build() откатился
# на устаревший кэш вместо сохранения почти пустого датасета.
MAX_FAILED_FRACTION = 0.5


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
        failed_lists: list[str] = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(fetch_list_rows, list_id): (title, list_id) for title, list_id in catalog}
            for future in as_completed(futures):
                title, list_id = futures[future]
                try:
                    rows_by_list[list_id] = future.result()
                except Exception as exc:
                    failed_lists.append(list_id)
                    print(
                        f"ВНИМАНИЕ: не удалось загрузить конкурсный список МЭИ {title!r} "
                        f"({list_id}) после исчерпания ретраев ({exc}) — направление "
                        "попадёт в пул с пустым списком абитуриентов (места и заголовок "
                        "сохранятся, но конкурс по нему не будет учтён в этом цикле сборки).",
                        file=sys.stderr,
                    )

        if catalog and len(failed_lists) / len(catalog) > MAX_FAILED_FRACTION:
            raise RuntimeError(
                f"Массовый сбой загрузки конкурсных списков МЭИ: не удалось загрузить "
                f"{len(failed_lists)} из {len(catalog)} ({len(failed_lists) / len(catalog):.0%}) — "
                f"превышен порог {MAX_FAILED_FRACTION:.0%}, похоже на системный сбой "
                "(троттлинг или недоступность сайта), а не единичный сетевой сбой одного "
                "списка. Сборка пула прервана, чтобы не перезаписать кэш почти пустым датасетом."
            )

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
