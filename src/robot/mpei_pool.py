from __future__ import annotations

import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

CATALOG_URL = "https://pk.mpei.ru/info/entrants_list"
KCP_URL = "https://pk.mpei.ru/info/speclist_simple.html"

_SECTION_START = "Бакалавриат очная форма обучения"
_SECTION_END = "Бакалавриат очно-заочная форма обучения"
_LIST_ID_RE = re.compile(r"entrants_list\d+\.html")

FETCH_RETRIES = 3
RETRYABLE_STATUS = {500, 502, 503, 504}


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
