from __future__ import annotations

import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

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
