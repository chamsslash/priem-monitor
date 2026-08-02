from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from ..config_loader import load_programs
from ..models import ProgramConfig
from ..parsers.utils import normalize_yes, to_float, to_int
from .direction_keys import fa_list_title
from .models import ProgramChoice, RobotPerson, RobotProgram

LIST_URL = "https://www.fa.ru/spiski/listabit.php"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
BASE_PARAMS = {
    "itype_list": "бкл",
    "type_conkurs": "Общий конкурс",
    "form_pay": "Бюджет",
}
POOL_SCOPE = "full"
PAGE_SIZE = 100
MIN_CATALOG_PROGRAMS = 30
CACHE_PATH = Path(__file__).resolve().parents[2] / "data" / "cache" / "fa_robot_pool.json"
CACHE_TTL_SEC = 7200
MAX_WORKERS = 6
FETCH_RETRIES = 3
RETRYABLE_STATUS = {500, 502, 503, 504}
UNTRACKED_FALLBACK_PLACES = 30  # аппроксимация мест ТОЛЬКО для непрофильных untracked-программ каталога ФА (вне охвата гарантии реальных мест)

# Места для программ вне config/programs.json (с сайта fa.ru, раздел программ).
FA_PLACES_OVERRIDES: dict[str, int] = {
    "Прикладная информатика, Бакалавр, Прикладные информационные системы в экономике и финансах, Очная": 25,
    "Бизнес-информатика, Бакалавр, Цифровая трансформация управления бизнесом, Очная": 20,
    "Инноватика, Бакалавр, Управление цифровыми инновациями, Очная": 25,
}


def _is_bachelor_day_title(title: str) -> bool:
    lowered = title.lower()
    if ", бакалавр," not in lowered:
        return False
    if ", очная" not in lowered:
        return False
    if any(marker in lowered for marker in ("дот", "заоч", "очно-заоч")):
        return False
    return True


class FaFullPool:
    FIELD_LABELS = {
        "code": "Уникальный код поступающего",
        "priority": "Приоритет зачисления, указанный поступающим по данной конкурсной группе",
        "is_bvi": "Без ВИ",
        "score": "Сумма конкурсных баллов",
        "consent": "Наличие согласия на зачисление",
    }

    def build(self, *, use_cache: bool = True) -> tuple[list[RobotPerson], list[RobotProgram], str, bool]:
        if use_cache:
            cached = self._load_cache()
            if cached is not None:
                return cached

        try:
            catalog = self._catalog_from_api()
            rows = self._fetch_all_rows(catalog)
            people = self._merge_people(rows)
            programs = self._build_programs(catalog)
            fetched_at = datetime.now(timezone.utc).isoformat()
            self._save_cache(people, programs, fetched_at)
            return people, programs, fetched_at, False
        except Exception:
            stale = self._load_cache(ignore_ttl=True)
            if stale is not None:
                return stale
            raise

    def _load_cache(
        self,
        *,
        ignore_ttl: bool = False,
    ) -> tuple[list[RobotPerson], list[RobotProgram], str, bool] | None:
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
                    "code": person.code,
                    "score": person.score,
                    "consent": person.consent,
                    "is_bvi": person.is_bvi,
                    "choices": [asdict(choice) for choice in person.choices],
                }
                for person in people
            ],
            "programs": [asdict(program) for program in programs],
        }
        CACHE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def _get(cls, params: dict[str, str | int]) -> str:
        last_error: Exception | None = None
        for attempt in range(FETCH_RETRIES):
            try:
                response = requests.get(
                    LIST_URL,
                    params=params,
                    headers={"User-Agent": USER_AGENT},
                    timeout=120,
                )
                response.raise_for_status()
                return response.text
            except requests.HTTPError as exc:
                last_error = exc
                status = exc.response.status_code if exc.response is not None else None
                if status in RETRYABLE_STATUS and attempt < FETCH_RETRIES - 1:
                    time.sleep(2**attempt)
                    continue
                break
            except requests.RequestException as exc:
                last_error = exc
                if attempt < FETCH_RETRIES - 1:
                    time.sleep(2**attempt)
                    continue
                break
        raise last_error or RuntimeError("Не удалось загрузить список Финуниверситета")

    @classmethod
    def _catalog_from_api(cls) -> list[str]:
        html = cls._get({**BASE_PARAMS, "action": "get_dict", "field": "facultet", "page": 1})
        payload = json.loads(html)
        titles = [str(item).strip() for item in payload.get("values", []) if str(item).strip()]
        catalog = [title for title in titles if _is_bachelor_day_title(title)]
        if not catalog:
            raise ValueError("Каталог программ Финуниверситета пуст")
        return sorted(catalog)

    def _fetch_all_rows(self, catalog: list[str]) -> list[dict]:
        rows: list[dict] = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(self._fetch_program_rows, title): title for title in catalog}
            for future in as_completed(futures):
                rows.extend(future.result())
        if not rows:
            raise ValueError("Список абитуриентов Финуниверситета пуст")
        return rows

    def _fetch_program_rows(self, title: str) -> list[dict]:
        page = 1
        total: int | None = None
        rows: list[dict] = []
        while True:
            html = self._get(
                {
                    **BASE_PARAMS,
                    "facultet": title,
                    "page": page,
                    "page_size": PAGE_SIZE,
                }
            )
            if total is None:
                match = re.search(r"total:\s*(\d+)", html)
                total = int(match.group(1)) if match else 0

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

                score = int(to_float(fields[self.FIELD_LABELS["score"]]) or 0)
                if score <= 0:
                    continue
                code = fields[self.FIELD_LABELS["code"]].strip()
                if not code:
                    continue
                rows.append(
                    {
                        "code": code,
                        "program_key": title,
                        "score": score,
                        "consent": normalize_yes(fields[self.FIELD_LABELS["consent"]]),
                        "is_bvi": normalize_yes(fields[self.FIELD_LABELS["is_bvi"]]),
                        "priority": to_int(fields[self.FIELD_LABELS["priority"]]) or 99,
                    }
                )

            if page_rows == 0 or page * PAGE_SIZE >= total:
                break
            page += 1
        return rows

    @staticmethod
    def _merge_people(rows: list[dict]) -> list[RobotPerson]:
        people: dict[str, RobotPerson] = {}
        for row in rows:
            choice = ProgramChoice(
                program_key=row["program_key"],
                priority=row["priority"],
                is_bvi=row["is_bvi"],
            )
            person = people.get(row["code"])
            if person is None:
                people[row["code"]] = RobotPerson(
                    code=row["code"],
                    score=row["score"],
                    consent=row["consent"],
                    is_bvi=row["is_bvi"],
                    choices=[choice],
                )
                continue
            person.score = max(person.score, row["score"])
            person.consent = person.consent or row["consent"]
            person.choices.append(choice)
            person.is_bvi = person.has_bvi_choice()
        return list(people.values())

    def _build_programs(self, catalog: list[str]) -> list[RobotProgram]:
        tracked = _tracked_title_map()
        places = _places_map()
        programs: list[RobotProgram] = []
        for title in catalog:
            programs.append(
                RobotProgram(
                    key=title,
                    title=title,
                    budget_places=places.get(title, UNTRACKED_FALLBACK_PLACES),
                    tracked_id=tracked.get(title),
                )
            )
        return programs


def _tracked_title_map() -> dict[str, int]:
    mapping: dict[str, int] = {}
    for program in tracked_programs():
        mapping[fa_list_title(program)] = program.id
    return mapping


def _places_map() -> dict[str, int]:
    places = {fa_list_title(program): program.budget_places for program in tracked_programs()}
    for title, budget_places in FA_PLACES_OVERRIDES.items():
        places.setdefault(title, budget_places)
    return places


def tracked_programs() -> list[ProgramConfig]:
    return [program for program in load_programs() if program.university == "Финансовый университет" and program.parser == "fa"]


def fetch_fa_full_pool(*, use_cache: bool = True) -> tuple[list[RobotPerson], list[RobotProgram], str, bool]:
    return FaFullPool().build(use_cache=use_cache)
