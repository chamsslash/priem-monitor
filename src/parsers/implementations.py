from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ..models import Applicant, ProgramConfig, ProgramResult
from .base import BaseParser
from .utils import header_index, normalize_yes, to_int


class MpeiParser(BaseParser):
    name = "mpei"

    def fetch(self, program: ProgramConfig) -> ProgramResult:
        fetched_at = datetime.now(timezone.utc).isoformat()
        try:
            list_url = program.list_url.split("#", 1)[0]
            response = requests.get(list_url, timeout=60)
            response.raise_for_status()
            response.encoding = "utf-8"
            soup = BeautifulSoup(response.text, "lxml")
            applicants = self._parse_table(soup)
            return ProgramResult(program=program, applicants=applicants, fetched_at=fetched_at)
        except Exception as exc:  # noqa: BLE001
            return ProgramResult(
                program=program,
                applicants=[],
                fetched_at=fetched_at,
                error=str(exc),
            )

    def _parse_table(self, soup: BeautifulSoup) -> list[Applicant]:
        tables = soup.find_all("table")
        if not tables:
            raise ValueError("Таблица конкурсного списка не найдена")

        table = max(tables, key=lambda item: len(item.find_all("tr")))
        rows = table.find_all("tr")
        if len(rows) < 3:
            raise ValueError("Таблица конкурсного списка МЭИ пуста")

        headers = self._expand_row_cells(rows[0])
        score_idx = header_index(headers, "сумма")
        consent_idx = header_index(headers, "согласие")
        priority_idx = header_index(headers, "приоритет")
        if score_idx is None or consent_idx is None or priority_idx is None:
            raise ValueError("Не удалось определить колонки таблицы МЭИ")

        applicants: list[Applicant] = []
        for row in rows[2:]:
            cells = self._expand_row_cells(row)
            if len(cells) <= max(score_idx, consent_idx, priority_idx):
                continue
            score = to_int(cells[score_idx])
            if not score or score <= 0:
                continue
            applicants.append(
                Applicant(
                    score=score,
                    consent=normalize_yes(cells[consent_idx]),
                    priority=to_int(cells[priority_idx]) or 99,
                )
            )
        if not applicants:
            raise ValueError("Список абитуриентов МЭИ пуст")
        return applicants

    @staticmethod
    def _expand_row_cells(row) -> list[str]:
        cells: list[str] = []
        for cell in row.find_all(["td", "th"]):
            text = cell.get_text(" ", strip=True)
            span = int(cell.get("colspan") or 1)
            cells.extend([text] * span)
        return cells


class MireaParser(BaseParser):
    name = "mirea"

    def fetch(self, program: ProgramConfig) -> ProgramResult:
        fetched_at = datetime.now(timezone.utc).isoformat()
        comp_id = program.parser_meta.get("comp_id")
        if not comp_id:
            return ProgramResult(
                program=program,
                applicants=[],
                fetched_at=fetched_at,
                error="Не указан comp_id для МИРЭА",
            )

        url = (
            "https://priem.mirea.ru/competitions_api/entrants"
            f"?competitions[]={comp_id}&edu_level=2"
        )
        try:
            payload = requests.get(url, timeout=60).json()
            competition = payload["data"][0]
            applicants = [
                Applicant(
                    score=int(item["finalMark"]) // 1000,
                    consent=bool(item.get("accepted")),
                    priority=int(item.get("priority") or 99),
                )
                for item in competition.get("entrants", [])
                if int(item.get("finalMark") or 0) // 1000 > 0
            ]
            return ProgramResult(
                program=program,
                applicants=applicants,
                fetched_at=fetched_at,
                source_places=int(competition.get("plan") or 0) or None,
            )
        except Exception as exc:  # noqa: BLE001
            return ProgramResult(
                program=program,
                applicants=[],
                fetched_at=fetched_at,
                error=str(exc),
            )


class ReaParser(BaseParser):
    name = "rea"

    _cached_key: str | None = None

    def fetch(self, program: ProgramConfig) -> ProgramResult:
        fetched_at = datetime.now(timezone.utc).isoformat()
        group_id = program.parser_meta.get("group_id")
        if not group_id:
            return ProgramResult(
                program=program,
                applicants=[],
                fetched_at=fetched_at,
                error="Не указан group_id для РЭУ",
            )

        try:
            api_key = self._get_api_key()
            url = (
                "https://abitrating.rea.ru/rest/v1/entrants"
                f"?select=*&competitive_group_id=eq.{group_id}"
                "&order=rating.asc&offset=0&limit=5000"
            )
            response = requests.get(
                url,
                headers={"apikey": api_key, "Authorization": f"Bearer {api_key}"},
                timeout=60,
            )
            response.raise_for_status()
            rows = response.json()
            applicants = [
                Applicant(
                    score=int(row.get("sum_mark") or 0),
                    consent=bool(row.get("agreement")),
                    priority=int(row.get("priority") or 99),
                )
                for row in rows
                if row.get("active", True)
            ]
            return ProgramResult(program=program, applicants=applicants, fetched_at=fetched_at)
        except Exception as exc:  # noqa: BLE001
            return ProgramResult(
                program=program,
                applicants=[],
                fetched_at=fetched_at,
                error=str(exc),
            )

    def _get_api_key(self) -> str:
        if self._cached_key:
            return self._cached_key
        js_url = "https://abitrating.rea.ru/assets/index-CToN5nqd.js"
        bundle = requests.get(js_url, timeout=60).text
        match = re.search(
            r"eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
            bundle,
        )
        if not match:
            raise ValueError("Не удалось получить API-ключ РЭУ")
        self._cached_key = match.group()
        return self._cached_key


class GubkinParser(BaseParser):
    name = "gubkin"
    _API_URL = "https://transfer.priem.gubkin.ru/abiturients_list/api/api.php"

    def fetch(self, program: ProgramConfig) -> ProgramResult:
        fetched_at = datetime.now(timezone.utc).isoformat()
        try:
            contest_group_id = program.parser_meta.get("contest_group_id")
            if not contest_group_id:
                contest_group_id = self._resolve_contest_group_id(program)
            education_type_id = int(program.parser_meta.get("education_type_id") or 1)
            rows = self._api_get(
                method="get",
                educationTypeId=education_type_id,
                contestGroupId=str(contest_group_id),
            )
            applicants = [
                Applicant(
                    score=int(row.get("totalBalls") or 0),
                    consent=bool(row.get("enrollmentAgreement")),
                    priority=int(row.get("priority") or 99),
                )
                for row in rows
                if int(row.get("totalBalls") or 0) > 0
            ]
            if not applicants:
                raise ValueError("Список РГУ им. Губкина пуст")
            return ProgramResult(program=program, applicants=applicants, fetched_at=fetched_at)
        except Exception as exc:  # noqa: BLE001
            return ProgramResult(
                program=program,
                applicants=[],
                fetched_at=fetched_at,
                error=str(exc),
            )

    def _resolve_contest_group_id(self, program: ProgramConfig) -> str:
        group_number = program.parser_meta.get("contest_group_number")
        faculty_id = program.parser_meta.get("faculty_id")
        education_form_id = int(program.parser_meta.get("education_form_id") or 1)
        if group_number is None or faculty_id is None:
            raise ValueError("Не указан contest_group_id для РГУ им. Губкина")

        groups = self._api_get(
            method="getGroups",
            educationFormId=education_form_id,
            facultyId=faculty_id,
        )
        target = f"конкурсная группа {group_number}"
        for group in groups:
            name = str(group.get("name") or group.get("full_name") or "").lower()
            if target in name:
                return str(group.get("id") or group.get("id_con"))
        raise ValueError(f"Конкурсная группа {group_number} не найдена в РГУ им. Губкина")

    def _api_get(self, **params: object) -> list[dict]:
        response = requests.get(self._API_URL, params={"act": "search", **params}, timeout=60)
        response.raise_for_status()
        payload = response.json()
        if not payload.get("success"):
            raise ValueError("API РГУ им. Губкина вернул ошибку")
        data = payload.get("data")
        return data if isinstance(data, list) else []


class UnsupportedParser(BaseParser):
    name = "unsupported"

    def fetch(self, program: ProgramConfig) -> ProgramResult:
        return ProgramResult(
            program=program,
            applicants=[],
            fetched_at=datetime.now(timezone.utc).isoformat(),
            error=f"Парсер для '{program.parser}' пока не реализован",
        )
