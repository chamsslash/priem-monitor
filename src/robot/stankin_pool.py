from __future__ import annotations

import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ..parsers.utils import header_index, normalize_yes, to_int

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


MAX_PAGES = 50  # защита от зацикливания, реальных страниц обычно 1-2


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
    return rows
