from __future__ import annotations

import base64
import re
from typing import Iterable
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from ..models import Applicant


def to_int(value: object) -> int | None:
    match = re.search(r"\d+", str(value or "").replace(" ", ""))
    return int(match.group()) if match else None


def to_float(value: object) -> float | None:
    text = str(value or "").replace(",", ".").strip()
    match = re.search(r"\d+(?:\.\d+)?", text)
    return float(match.group()) if match else None


def normalize_yes(value: object) -> bool:
    text = str(value or "").strip()
    if text in {"✓", "✔", "+", "☑", "V", "v"}:
        return True
    return text.lower() in {"1", "true", "yes", "да", "y", "есть", "подано"}


def header_index(headers: Iterable[str], *names: str) -> int | None:
    normalized = [re.sub(r"\s+", " ", h.strip().lower()) for h in headers]
    targets = [re.sub(r"\s+", " ", name.strip().lower()) for name in names]
    for index, header in enumerate(normalized):
        if any(target in header for target in targets):
            return index
    return None


def parse_table_by_headers(soup: BeautifulSoup, mapping: dict[str, tuple[str, ...]]) -> list[Applicant]:
    table = soup.find("table")
    if table is None:
        raise ValueError("Таблица не найдена")

    rows = table.find_all("tr")
    if not rows:
        return []

    headers = [cell.get_text(" ", strip=True) for cell in rows[0].find_all(["td", "th"])]
    indexes = {key: header_index(headers, *names) for key, names in mapping.items()}

    applicants: list[Applicant] = []
    for row in rows[1:]:
        cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["td", "th"])]
        if not cells or not any(cells):
            continue

        score_idx = indexes.get("score")
        score_raw = cells[score_idx] if score_idx is not None and score_idx < len(cells) else ""
        score = to_int(score_raw) or int(to_float(score_raw) or 0)
        if score <= 0:
            continue

        consent_idx = indexes.get("consent")
        priority_idx = indexes.get("priority")
        consent = normalize_yes(cells[consent_idx]) if consent_idx is not None and consent_idx < len(cells) else False
        priority = to_int(cells[priority_idx]) if priority_idx is not None and priority_idx < len(cells) else 99
        applicants.append(Applicant(score=score, consent=consent, priority=priority or 99))
    return applicants


def decode_mospolytech_qs(list_url: str) -> dict[str, str]:
    query = parse_qs(urlparse(list_url).query)
    raw = query.get("qs", [""])[0]
    if not raw:
        raise ValueError("В URL Московского политеха нет параметра qs")
    padded = raw + "=" * (-len(raw) % 4)
    parts = base64.b64decode(padded).decode("utf-8").split("|")
    if len(parts) != 4:
        raise ValueError(f"Неожиданный формат qs: {parts}")
    return {
        "select1": parts[0],
        "specCode": parts[1],
        "eduForm": parts[2],
        "eduFin": parts[3],
    }


def extract_degree_id(list_url: str) -> str:
    match = re.search(r"/degrees/(\d+)", list_url)
    if not match:
        raise ValueError("Не удалось определить id направления РУТ")
    return match.group(1)
