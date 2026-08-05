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
# Порог «кэш неполный, пересобрать». Считаем только бюджетные конкурсы, их 84
# (до отсечения платных было 181, и порог стоял 150 — после фикса он отбраковывал
# заведомо годный кэш). Берём с запасом вниз: вуз может закрыть часть направлений.
MIN_CATALOG_PROGRAMS = 60
CACHE_PATH = Path(__file__).resolve().parents[2] / "data" / "cache" / "mirea_robot_pool.json"
CACHE_TTL_SEC = 7200
CHUNK_SIZE = 3
MAX_WORKERS = 5
FETCH_RETRIES = 3
RETRYABLE_STATUS = {500, 502, 503, 504}


def _program_title(program: ProgramConfig) -> str:
    return program.program.replace("\n", " / ").strip() or program.competition_group.strip()


# У МИРЭА бюджетный и ПЛАТНЫЙ конкурсы подписаны ОДИНАКОВО — compType «общий
# конкурс», поэтому отличать их можно только по compTypeId: «4» — бюджет,
# «6» — платное (ещё «5» — БВИ). Раньше фильтр смотрел на текст и тянул оба, из-за
# чего в модель попадали 97 платных «направлений» и 7723 фантомных места против
# 2304 настоящих. Каскад рассаживал людей на платные места, они исчезали из борьбы
# за бюджет, и робот видел свободные места там, где их нет: обещал проходной балл
# 259 там, где сайт показывал 263. Признак сверен со страницей приёмной комиссии
# на 26 программах — бюджетные и платные числа совпали все.
BUDGET_COMP_TYPE_ID = "4"

# Квотных недоборов у МИРЭА мы не моделируем, и это не упущение: в выдаче API
# теми же параметрами есть РОВНО три типа конкурса — «4» бюджет общий (88
# конкурсов, 2304 места), «5» БВИ и «6» платное. Конкурсов по особой, отдельной
# и целевой квотам там нет вовсе, то есть посчитать, сколько квотных мест
# осталось незанятыми, попросту не из чего. Ср. МЭИ, где квотные списки
# публикуются отдельно и недобор считается (mpei_pool.compute_quota_shortfall).


@dataclass
class MireaCompetition:
    comp_id: str
    title: str
    subject: str
    plan: int
    # «Проходной ВП» с сайта: минимальный балл среди проходящих. Живой оракул для
    # сверки прогноза — то же, что passing_cutoff у СТАНКИНа.
    min_score: int | None = None


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
                # Только бюджетный общий конкурс. Платный (compTypeId «6») в модель
                # не берём совсем: робот считает поступление на бюджет, и платные
                # места не должны уводить конкурентов из бюджетной очереди.
                if str(competition.get("compTypeId")) != BUDGET_COMP_TYPE_ID:
                    continue
                plan = int(competition.get("plan") or 0)
                if plan <= 0:
                    continue
                min_score = competition.get("minScore")
                for comp_id in competition.get("compIds", []):
                    catalog.append(
                        MireaCompetition(
                            comp_id=str(comp_id),
                            title=display,
                            subject=subject,
                            plan=plan,
                            min_score=int(min_score) if min_score else None,
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
                    min_score=item.min_score,
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
    def _is_bvi(entrant: dict) -> bool:
        """Право поступить БЕЗ вступительных испытаний — поле isBVI.

        Раньше сюда подставлялось поле pc («Преим. право»), и это ломало модель:
        преимущественное право — всего лишь тай-брейк при РАВНЫХ баллах, а фаза
        БВИ в каскаде занимает места вперёд всех независимо от балла. В итоге
        1382 человека из 21611 (по 9 проверенным конкурсам) забирали бюджетные
        места раньше тех, кто набрал больше, и проходной балл робота оказывался
        на десятки баллов ниже реального: по МИРЭА в среднем 58 баллов разрыва
        с проходным баллом сайта.

        В бюджетном общем конкурсе isBVI=0 у всех: олимпиадники идут отдельным
        конкурсом (compTypeId «5»), которого в пуле нет. Поле оставлено, а не
        выброшено, чтобы модель не сломалась, если вуз начнёт помечать БВИ
        прямо в общем конкурсе.
        """
        return str(entrant.get("isBVI")) == "1"

    @staticmethod
    def _enrolls_elsewhere(entrant: dict) -> bool:
        """Сайт уже вычеркнул человека отсюда: «Исключен (зачислен на другой
        конкурс)». Как «Зачисляется в другой КГ» у МЭИ — место, которое он тут
        «занимает», на самом деле свободно."""
        return "Исключен" in str(entrant.get("s") or "")

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
                is_bvi = MireaFullPool._is_bvi(entrant)
                priority = int(entrant.get("priority") or 99)
                choice = ProgramChoice(
                    program_key=comp_id,
                    priority=priority or 99,
                    is_bvi=is_bvi,
                    enrolls_elsewhere=MireaFullPool._enrolls_elsewhere(entrant),
                )
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
                    # Места приходят живьём из competitions_api (поле plan), а не из
                    # конфига-резерва: раньше штамп не ставился, и сверка ошибочно
                    # рапортовала «места из резерва» при живых и верных числах.
                    seat_source="live",
                    passing_cutoff=item.min_score,
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


def read_mirea_cached_pool() -> tuple[list[RobotPerson], list[RobotProgram], str, bool] | None:
    """Отдаёт кэш МИРЭА ЛЮБОГО возраста и никогда не ходит в сеть. См. read_fa_cached_pool."""
    return MireaFullPool()._load_cache(ignore_ttl=True)
