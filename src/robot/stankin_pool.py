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
from ..parsers.remaining import StankinParser
from ..parsers.utils import header_index, normalize_yes, to_int
from .models import ProgramChoice, RobotPerson, RobotProgram

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


MAX_PAGES = 300  # запас: живьём на 09.03.04 список доходил до 61 страницы (2967 строк), взят кратный запас


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

        next_link = soup.find("a", class_="main-ui-pagination-next")
        href = next_link.get("href") if next_link else None
        if not href:
            break
        response = _get(urljoin(response.url, href))
    else:
        print(
            f"ВНИМАНИЕ: пагинация СТАНКИНа для направления {direction!r} "
            f"не завершилась естественно за MAX_PAGES={MAX_PAGES} страниц — "
            "список мог быть обрезан.",
            file=sys.stderr,
        )
    return rows


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

        catalog_set = set(catalog)
        for direction, configs in direction_groups.items():
            if direction not in catalog_set:
                ids = sorted(p.id for p in configs)
                print(
                    f"ВНИМАНИЕ: отслеживаемое направление СТАНКИНа {direction!r} "
                    f"(id {ids}, ключ пула {tracked_key_by_direction[direction][0]!r}) "
                    "не найдено среди реальных направлений каталога сайта — оно не "
                    "попадёт в пул, и приоритеты Димы на него тихо выпадут из "
                    "симуляции (возможно, сайт переименовал или убрал эту строку "
                    "каталога).",
                    file=sys.stderr,
                )

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
                    if len(configs) > 1:
                        breakdown = "+".join(
                            str(p.budget_places) for p in sorted(configs, key=lambda p: p.id)
                        )
                        print(
                            f"ВНИМАНИЕ: для смёрженной пары СТАНКИНа {direction!r} "
                            f"(ключ {key!r}) официальный КЦП не найден — используется "
                            f"fallback-сумма квот профилей из конфига {breakdown}={places}, "
                            "которая может завышать реальное общее число бюджетных мест.",
                            file=sys.stderr,
                        )
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
