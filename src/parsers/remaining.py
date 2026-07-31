from __future__ import annotations

import re
import urllib.parse
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from ..models import Applicant, ProgramConfig, ProgramResult
from .base import BaseParser
from .utils import (
    decode_mospolytech_qs,
    extract_degree_id,
    header_index,
    normalize_yes,
    parse_table_by_headers,
    to_float,
    to_int,
)

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


class MiitParser(BaseParser):
    name = "miit"

    def fetch(self, program: ProgramConfig) -> ProgramResult:
        fetched_at = datetime.now(timezone.utc).isoformat()
        try:
            degree_id = program.parser_meta.get("degree_id") or extract_degree_id(program.list_url)
            url = f"https://www.miit.ru/api-1c/miit/hs/AdmissionPlan/rating?id={degree_id}&year=2026"
            response = requests.get(
                url,
                headers={"User-Agent": USER_AGENT, "Referer": program.list_url},
                timeout=60,
            )
            response.raise_for_status()
            payload = response.json()
            applicants: list[Applicant] = []
            for competition in payload.get("competitions", []):
                if "общий" not in competition.get("competitionName", "").lower():
                    continue
                for group in competition.get("concourseEntrantGroups", []):
                    group_name = str(group.get("name", ""))
                    if not group_name.startswith("Участвуют в конкурсе"):
                        continue
                    for entrant in group.get("entrants", []):
                        disc = int(entrant.get("pointsDisc") or entrant.get("points") or 0)
                        ind = int(entrant.get("pointsInd") or 0)
                        if disc <= 0:
                            continue
                        score = disc + ind
                        consent = str(
                            entrant.get("originalDocumentStatusShort")
                            or entrant.get("originalDocumentStatus")
                            or ""
                        ) == "Представлено"
                        priority = to_int(entrant.get("preferenceIndexDisplay")) or 99
                        applicants.append(Applicant(score=score, consent=consent, priority=priority))
            if not applicants:
                raise ValueError("Список абитуриентов РУТ пуст")
            return ProgramResult(program=program, applicants=applicants, fetched_at=fetched_at)
        except Exception as exc:  # noqa: BLE001
            return ProgramResult(program=program, applicants=[], fetched_at=fetched_at, error=str(exc))


class StankinParser(BaseParser):
    name = "stankin"

    STANKIN_PROGRAMS = {
        "программная инженерия": "09.03.04 Программная инженерия",
        "разработка программных комплексов в рамках": "09.03.01.01 Разработка программных комплексов",
        "разработка программных комплексов": "09.03.01.01 Разработка программных комплексов",
        "математическое и компьютерное моделирование": "09.03.03.01 Математическое и компьютерное моделирование процессов и систем",
        "управление данными": "09.03.03.02 Управление данными",
        "цифровые системы управления": "09.03.02.01 Разработка и внедрение корпоративных информационных систем",
        "разработка и внедрение корпоративных": "09.03.02.01 Разработка и внедрение корпоративных информационных систем",
    }

    def fetch(self, program: ProgramConfig) -> ProgramResult:
        fetched_at = datetime.now(timezone.utc).isoformat()
        try:
            property_394 = program.filter_rules.get("property_394") or self._resolve_program(program)
            params = {
                "PROPERTY_388": "Бюджетная основа",
                "PROPERTY_389": "1 - Очная",
                "PROPERTY_394": property_394,
                "PROPERTY_423": "",
                "PROPERTY_402": "-",
                "COL_CITIZENSHIP": "Гражданин РФ",
                "PROPERTY_747": "-",
                "apply_filter": "Y",
                "PROPERTY_584": "ready",
                "PROPERTY_710": "",
                "PROPERTY_410": "",
                "LIST_TYPE": "ranked",
                "EDU_LEVEL": "bs",
                "PROPERTY_418": "Прием на обучение на бакалавриат/специалитет",
            }
            response = requests.get(
                "https://priem.stankin.ru/gridspisokpostupayushchikh",
                params=params,
                timeout=60,
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")
            applicants = parse_table_by_headers(
                soup,
                {
                    "score": ("сумма баллов с ид",),
                    "consent": ("согласие на зачисление",),
                    "priority": ("приоритет",),
                },
            )
            if not applicants:
                raise ValueError("Список СТАНКИН пуст")
            return ProgramResult(program=program, applicants=applicants, fetched_at=fetched_at)
        except Exception as exc:  # noqa: BLE001
            return ProgramResult(program=program, applicants=[], fetched_at=fetched_at, error=str(exc))

    def _resolve_program(self, program: ProgramConfig) -> str:
        if program.filter_rules.get("property_394"):
            return program.filter_rules["property_394"]
        text = f"{program.program} {program.competition_group}".lower()
        for key, value in self.STANKIN_PROGRAMS.items():
            if key in text:
                return value
        code = program.direction_code
        if code == "38055":
            return "09.03.04 Программная инженерия"
        if code == "36959":
            return "09.03.01.01 Разработка программных комплексов"
        if code == "37689":
            return "09.03.03.01 Математическое и компьютерное моделирование процессов и систем"
        if code == "37324":
            return "09.03.02.01 Разработка и внедрение корпоративных информационных систем"
        raise ValueError(f"Не удалось сопоставить направление СТАНКИН: {program.program[:60]}")


class MospolytechParser(BaseParser):
    name = "mospolytech"

    def fetch(self, program: ProgramConfig) -> ProgramResult:
        fetched_at = datetime.now(timezone.utc).isoformat()
        try:
            post_data = program.parser_meta or decode_mospolytech_qs(program.list_url)
            session = requests.Session()
            session.headers.update(
                {
                    "User-Agent": USER_AGENT,
                    "Referer": "https://mospolytech.ru/postupayushchim/priem-v-universitet/rating-abiturientov/",
                }
            )
            response = session.post(
                "https://mospolytech.ru/postupayushchim/priem-v-universitet/rating-abiturientov/fio_list_curl.php",
                data={**post_data, "f": "1"},
                timeout=120,
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")
            table = self._pick_applicants_table(soup)
            if table is None:
                raise ValueError("Таблица Московского политеха не найдена")

            headers = [cell.get_text(" ", strip=True) for cell in table.find_all("tr")[0].find_all(["td", "th"])]
            score_idx = headers.index("Конкурсный балл")
            consent_idx = headers.index("Согласие")
            priority_idx = headers.index("Приоритет")

            applicants: list[Applicant] = []
            for row in table.find_all("tr")[1:]:
                cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["td", "th"])]
                if len(cells) <= max(score_idx, consent_idx, priority_idx):
                    continue
                score = to_int(cells[score_idx])
                if not score:
                    continue
                applicants.append(
                    Applicant(
                        score=score,
                        consent=normalize_yes(cells[consent_idx]) or cells[consent_idx].strip() in {"✓", "+"},
                        priority=to_int(cells[priority_idx]) or 99,
                    )
                )
            if not applicants:
                raise ValueError("Список Московского политеха пуст")
            return ProgramResult(program=program, applicants=applicants, fetched_at=fetched_at)
        except Exception as exc:  # noqa: BLE001
            return ProgramResult(program=program, applicants=[], fetched_at=fetched_at, error=str(exc))

    @staticmethod
    def _pick_applicants_table(soup: BeautifulSoup):
        candidates = []
        for table in soup.find_all("table", class_="check"):
            rows = table.find_all("tr")
            if len(rows) < 5:
                continue
            headers = [cell.get_text(" ", strip=True) for cell in rows[0].find_all(["td", "th"])]
            if "Конкурсный балл" in headers:
                candidates.append((len(rows), table))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]


class RanepaParser(BaseParser):
    name = "ranepa"
    _TABLE_SELECTOR = "table.ranepa-table tbody tr"

    def fetch(self, program: ProgramConfig) -> ProgramResult:
        fetched_at = datetime.now(timezone.utc).isoformat()
        try:
            html = self._load_html(program.list_url)
            soup = BeautifulSoup(html, "lxml")
            table = soup.find("table", class_="ranepa-table") or soup.find("table")
            if table is None:
                raise ValueError("Таблица РАНХиГС не найдена")

            rows = table.find_all("tr")
            headers = [cell.get_text(" ", strip=True) for cell in rows[0].find_all(["td", "th"])]
            score_idx = header_index(headers, "сумма конкурсных баллов", "конкурсных баллов")
            consent_idx = header_index(headers, "наличие согласия", "согласия")
            priority_idx = header_index(headers, "приоритет зачисления", "приоритет")
            if score_idx is None or consent_idx is None or priority_idx is None:
                raise ValueError("Не удалось определить колонки таблицы РАНХиГС")

            applicants: list[Applicant] = []
            for row in rows[1:]:
                cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["td", "th"])]
                if not cells or not to_int(cells[0]):
                    continue
                score = int(to_float(cells[score_idx]) or 0)
                if score <= 0:
                    continue
                applicants.append(
                    Applicant(
                        score=score,
                        consent=normalize_yes(cells[consent_idx]),
                        priority=to_int(cells[priority_idx]) or 99,
                    )
                )
            if not applicants:
                raise ValueError("Список РАНХиГС пуст")
            return ProgramResult(program=program, applicants=applicants, fetched_at=fetched_at)
        except Exception as exc:  # noqa: BLE001
            return ProgramResult(program=program, applicants=[], fetched_at=fetched_at, error=str(exc))

    @classmethod
    def _load_html(cls, url: str) -> str:
        last_error: Exception | None = None
        for _ in range(3):
            try:
                return cls._fetch_page_html(url)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        raise last_error or ValueError("Таблица РАНХиГС не найдена")

    @classmethod
    def _fetch_page_html(cls, url: str) -> str:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=120000)
                page.wait_for_selector(cls._TABLE_SELECTOR, timeout=120000)
                html = page.content()
            finally:
                browser.close()
        if "ranepa-table" not in html:
            raise ValueError("Таблица РАНХиГС не найдена")
        return html


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


class MaiParser(BaseParser):
    name = "mai"

    GROUP_TO_SPEC = {
        "программная инженерия": "Программная инженерия",
        "прикладная математика и информатика": "Прикладная математика и информатика",
        "фундаментальная информатика и информационные технологии": "Фундаментальная информатика и информационные технологии",
        "прикладная информатика": "Прикладная информатика",
        "прикладная математика": "Прикладная математика",
        "информационные системы и технологии": "Информационные системы и технологии",
        "информатика и вычислительная техника": "Информатика и вычислительная техника",
    }

    def fetch(self, program: ProgramConfig) -> ProgramResult:
        fetched_at = datetime.now(timezone.utc).isoformat()
        try:
            html = self._load_list_html(program)
            applicants = self._parse_list_html(html, program)
            if not applicants:
                raise ValueError("Список МАИ пуст")
            return ProgramResult(program=program, applicants=applicants, fetched_at=fetched_at)
        except Exception as exc:  # noqa: BLE001
            return ProgramResult(program=program, applicants=[], fetched_at=fetched_at, error=str(exc))

    def _session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT, "Referer": "https://priem.mai.ru/list/"})
        return session

    def _fetch_data_html(self, session: requests.Session, slug: str) -> str:
        response = session.get(f"https://public.mai.ru/priem/list/data/{slug}.html", timeout=60)
        response.encoding = "utf-8"
        response.raise_for_status()
        return response.text

    @staticmethod
    def _decode_option_text(text: str) -> str:
        try:
            return text.encode("latin1").decode("utf-8")
        except UnicodeError:
            return text

    def _load_list_html(self, program: ProgramConfig) -> str:
        session = self._session()
        main = session.get("https://priem.mai.ru/list/", timeout=60).text
        prefix_match = re.search(r'value="(p\d+_1)"[^>]*>МАИ', main)
        if not prefix_match:
            raise ValueError("Не удалось определить префикс списков МАИ")
        prefix = prefix_match.group(1)

        level_html = self._fetch_data_html(session, f"{prefix}_l1")
        options = [
            (value, self._decode_option_text(text))
            for value, text in re.findall(r'<option value="([^"]+)"[^>]*>([^<]+)</option>', level_html)
            if value != "0"
        ]
        target_name = self._resolve_group_name(program)
        spec_value = next((value for value, text in options if text == target_name), None)
        if spec_value is None:
            spec_value = next(
                (value for value, text in options if target_name.lower() in text.lower()),
                None,
            )
        if spec_value is None:
            raise ValueError(f"Направление МАИ не найдено в списке: {target_name}")

        form_html = self._fetch_data_html(session, spec_value)
        form_match = re.search(r'value="([^"]*_f1)"', form_html)
        if not form_match:
            raise ValueError("Не найдена очная форма обучения в МАИ")
        pay_html = self._fetch_data_html(session, form_match.group(1))
        pay_match = re.search(r'value="([^"]*_p1)"', pay_html)
        if not pay_match:
            raise ValueError("Не найден бюджетный набор в МАИ")
        return self._fetch_data_html(session, pay_match.group(1))

    def _resolve_group_name(self, program: ProgramConfig) -> str:
        group = program.competition_group.lower()
        for key, value in self.GROUP_TO_SPEC.items():
            if key in group:
                return value
        return program.competition_group

    def _parse_list_html(self, html: str, program: ProgramConfig) -> list[Applicant]:
        soup = BeautifulSoup(html, "lxml")
        table = max(
            soup.find_all("table"),
            key=lambda item: len(item.find_all("tr")),
            default=None,
        )
        if table is None:
            raise ValueError("Таблица МАИ не найдена")

        rows = table.find_all("tr")
        headers = [cell.get_text(" ", strip=True).lower() for cell in rows[0].find_all(["td", "th"])]
        score_idx = next((i for i, h in enumerate(headers) if "сумма баллов" in h and "ид" not in h and "предмет" not in h), 2)
        consent_idx = next((i for i, h in enumerate(headers) if "соглас" in h), None)
        priority_idx = next((i for i, h in enumerate(headers) if "приоритет" in h), None)
        profile_idx = next((i for i, h in enumerate(headers) if any(x in h for x in ("програм", "профил", "направ"))), None)

        keywords = program.filter_rules.get("program_keywords") or self._default_keywords(program)
        applicants: list[Applicant] = []
        for row in rows[1:]:
            cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["td", "th"])]
            if not cells or not to_int(cells[0]):
                continue
            if program.needs_filter and profile_idx is not None and profile_idx < len(cells):
                profile_text = cells[profile_idx].lower()
                if not any(keyword.lower() in profile_text for keyword in keywords):
                    continue
            score = to_int(cells[score_idx]) if score_idx < len(cells) else None
            if not score:
                continue
            consent_raw = cells[consent_idx] if consent_idx is not None and consent_idx < len(cells) else ""
            consent = normalize_yes(consent_raw) or consent_raw.strip() in {"✓", "+"}
            priority = to_int(cells[priority_idx]) if priority_idx is not None and priority_idx < len(cells) else 99
            applicants.append(Applicant(score=score, consent=consent, priority=priority or 99))
        return applicants

    @staticmethod
    def _default_keywords(program: ProgramConfig) -> list[str]:
        text = program.program.replace("\n", " ")
        if "1 список" in text or "2 разные" in text or "3 разных" in text or "4 разных" in text:
            parts = re.split(r",|\n|\(", text)
            return [part.strip() for part in parts if part.strip() and "список" not in part.lower()][:1]
        first = text.split("(")[0].strip()
        return [first] if first else [program.competition_group]
