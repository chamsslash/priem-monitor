from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests

from ..config_loader import load_programs
from ..models import ProgramConfig
from .models import ProgramChoice, RobotPerson, RobotProgram


ENTRANTS_URL = "https://priem.mirea.ru/competitions_api/entrants"
CATALOG_URL = "https://priem.mirea.ru/competitions_api"
CATALOG_PARAMS = {
    "edu_level_id": 2,
    "edu_form_id": 1,
    "org_unit_id": "1484028700495285107",
}
# МИРЭА (priem.mirea.ru за DDoS-Guard) режет не-RU IP по гео. С зарубежного
# хостинга запросы к МИРЭА идут через SOCKS5-прокси на домашний RU-канал (reverse
# SSH-туннель). Адрес прокси — из env MIREA_PROXY (напр. socks5h://127.0.0.1:1080,
# socks5h — чтобы и DNS резолвился на домашней стороне). Не задан → прямой доступ
# (локально с RU IP всё работает и так). Только МИРЭА: остальные 3 вуза с
# зарубежного IP отвечают нормально, им прокси не нужен.
_MIREA_PROXY = os.environ.get("MIREA_PROXY", "").strip()
_PROXIES = {"http": _MIREA_PROXY, "https": _MIREA_PROXY} if _MIREA_PROXY else None

POOL_SCOPE = "full"
MIN_CATALOG_PROGRAMS = 150
CACHE_PATH = Path(__file__).resolve().parents[2] / "data" / "cache" / "mirea_robot_pool.json"
CACHE_TTL_SEC = 7200
CHUNK_SIZE = 3
MAX_WORKERS = 5
FETCH_RETRIES = 3
RETRYABLE_STATUS = {500, 502, 503, 504}


def _program_title(program: ProgramConfig) -> str:
    return program.program.replace("\n", " / ").strip() or program.competition_group.strip()


@dataclass
class MireaCompetition:
    comp_id: str
    title: str
    subject: str
    plan: int


class MireaFullPool:
    def build(self, *, use_cache: bool = True) -> tuple[list[RobotPerson], list[RobotProgram], str, bool]:
        if use_cache:
            cached = self._load_cache()
            if cached is not None:
                return cached

        try:
            catalog = self._catalog_from_api()
            competitions = self._fetch_entrants([item.comp_id for item in catalog])
            catalog = self._apply_plans_from_api(catalog, competitions)
            people = self._merge_people(competitions)
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

    @staticmethod
    def _catalog_from_api() -> list[MireaCompetition]:
        response = requests.get(CATALOG_URL, params=CATALOG_PARAMS, timeout=60, proxies=_PROXIES)
        response.raise_for_status()
        catalog: list[MireaCompetition] = []
        for program in response.json():
            title = (program.get("title") or "").strip()
            subject = (program.get("programSubjectTitle") or "").strip()
            display = f"{title} ({subject})" if subject and subject not in title else title
            for competition in program.get("competitions", []):
                if competition.get("compType") != "общий конкурс":
                    continue
                plan = int(competition.get("plan") or 0)
                if plan <= 0:
                    continue
                for comp_id in competition.get("compIds", []):
                    catalog.append(
                        MireaCompetition(
                            comp_id=str(comp_id),
                            title=display,
                            subject=subject,
                            plan=plan,
                        )
                    )
        if not catalog:
            raise ValueError("Каталог конкурсов МИРЭА пуст")
        return catalog

    @staticmethod
    def _catalog_from_tracked() -> list[MireaCompetition]:
        catalog: list[MireaCompetition] = []
        for program in tracked_programs():
            comp_id = program.parser_meta.get("comp_id")
            if not comp_id:
                raise ValueError(f"Не указан comp_id для программы МИРЭА id={program.id}")
            catalog.append(
                MireaCompetition(
                    comp_id=str(comp_id),
                    title=_program_title(program),
                    subject=program.competition_group,
                    plan=program.budget_places,
                )
            )
        if not catalog:
            raise ValueError("Нет отслеживаемых программ МИРЭА в config/programs.json")
        return catalog

    @staticmethod
    def _apply_plans_from_api(
        catalog: list[MireaCompetition],
        competitions: list[dict],
    ) -> list[MireaCompetition]:
        plans = {str(item.get("id") or ""): int(item.get("plan") or 0) for item in competitions}
        updated: list[MireaCompetition] = []
        for item in catalog:
            plan = plans.get(item.comp_id) or item.plan
            updated.append(
                MireaCompetition(
                    comp_id=item.comp_id,
                    title=item.title,
                    subject=item.subject,
                    plan=plan,
                )
            )
        return updated

    @classmethod
    def _fetch_chunk(cls, comp_ids: list[str]) -> list[dict]:
        params = [("competitions[]", comp_id) for comp_id in comp_ids] + [("edu_level", "2")]
        last_error: Exception | None = None
        for attempt in range(FETCH_RETRIES):
            try:
                response = requests.get(ENTRANTS_URL, params=params, timeout=180, proxies=_PROXIES)
                response.raise_for_status()
                return response.json().get("data", [])
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

        if len(comp_ids) == 1:
            raise last_error or RuntimeError("Не удалось загрузить список МИРЭА")

        results: list[dict] = []
        for comp_id in comp_ids:
            results.extend(cls._fetch_chunk([comp_id]))
        return results

    def _fetch_entrants(self, comp_ids: list[str]) -> list[dict]:
        chunks = [comp_ids[index : index + CHUNK_SIZE] for index in range(0, len(comp_ids), CHUNK_SIZE)]
        competitions: list[dict] = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(self._fetch_chunk, chunk) for chunk in chunks]
            for future in as_completed(futures):
                competitions.extend(future.result())
        return competitions

    @staticmethod
    def _has_preferential_right(entrant: dict) -> bool:
        # В API колонка «Преим. право» на сайте = поле pc (не iHP/iHPO — это «высший приоритет»).
        return str(entrant.get("pc")) == "1"

    @staticmethod
    def _merge_people(competitions: list[dict]) -> list[RobotPerson]:
        people: dict[str, RobotPerson] = {}
        for competition in competitions:
            comp_id = str(competition.get("id") or "")
            if not comp_id:
                continue
            for entrant in competition.get("entrants", []):
                score = int(entrant.get("finalMark") or 0) // 1000
                if score <= 0:
                    continue
                code = str(entrant.get("superCode") or entrant.get("id") or "").strip()
                if not code:
                    continue
                consent = bool(entrant.get("accepted"))
                is_bvi = MireaFullPool._has_preferential_right(entrant)
                priority = int(entrant.get("priority") or 99)
                choice = ProgramChoice(program_key=comp_id, priority=priority or 99, is_bvi=is_bvi)
                person = people.get(code)
                if person is None:
                    people[code] = RobotPerson(
                        code=code,
                        score=score,
                        consent=consent,
                        is_bvi=is_bvi,
                        choices=[choice],
                    )
                    continue
                person.score = max(person.score, score)
                person.consent = person.consent or consent
                person.choices.append(choice)
                person.is_bvi = person.has_bvi_choice()
        return list(people.values())

    def _build_programs(self, catalog: list[MireaCompetition]) -> list[RobotProgram]:
        tracked = _tracked_comp_map()
        programs: list[RobotProgram] = []
        for item in catalog:
            programs.append(
                RobotProgram(
                    key=item.comp_id,
                    title=item.title,
                    budget_places=item.plan,
                    tracked_id=tracked.get(item.comp_id),
                )
            )
        return sorted(programs, key=lambda program: program.title)


def _tracked_comp_map() -> dict[str, int]:
    mapping: dict[str, int] = {}
    for program in tracked_programs():
        comp_id = program.parser_meta.get("comp_id")
        if comp_id:
            mapping[str(comp_id)] = program.id
    return mapping


def tracked_programs() -> list[ProgramConfig]:
    return [program for program in load_programs() if program.university == "МИРЭА" and program.parser == "mirea"]


def fetch_mirea_full_pool(*, use_cache: bool = True) -> tuple[list[RobotPerson], list[RobotProgram], str, bool]:
    return MireaFullPool().build(use_cache=use_cache)
