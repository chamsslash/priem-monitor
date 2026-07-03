from __future__ import annotations

import json
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .parsers.utils import decode_mospolytech_qs, extract_degree_id


def _read_xlsx(path: Path) -> list[list[str]]:
    with zipfile.ZipFile(path) as zf:
        ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        shared = ET.fromstring(zf.read("xl/sharedStrings.xml"))
        strings: list[str] = []
        for item in shared.findall("m:si", ns):
            parts = [node.text or "" for node in item.findall(".//m:t", ns)]
            strings.append("".join(parts))

        sheet = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
        rows: list[list[str]] = []
        for row in sheet.findall(".//m:sheetData/m:row", ns):
            values: list[str] = []
            for cell in row.findall("m:c", ns):
                cell_type = cell.get("t")
                value_node = cell.find("m:v", ns)
                if value_node is None:
                    values.append("")
                elif cell_type == "s":
                    values.append(strings[int(value_node.text)])
                else:
                    values.append(value_node.text or "")
            rows.append(values)
        return rows


def _detect_parser(url: str) -> tuple[str, dict]:
    if "priem.mirea.ru" in url:
        comp_id = parse_qs(urlparse(url).query).get("comp_ids", [""])[0]
        return "mirea", {"comp_id": comp_id}
    if "abitrating.rea.ru" in url:
        return "rea", {"group_id": url.rstrip("/").split("/")[-1]}
    if "pk.mpei.ru" in url:
        return "mpei", {}
    if "miit.ru" in url:
        return "miit", {"degree_id": extract_degree_id(url)}
    if "mospolytech.ru" in url and "qs=" in url:
        try:
            return "mospolytech", decode_mospolytech_qs(url)
        except ValueError:
            return "mospolytech", {}
    if "priem.mai.ru" in url:
        return "mai", {}
    if "priem.stankin.ru" in url:
        return "stankin", {}
    if "fa.ru" in url:
        return "fa", {}
    if "my.ranepa.ru" in url:
        return "ranepa", {}
    if "priem.gubkin.ru" in url or "transfer.priem.gubkin.ru" in url:
        return "gubkin", {}
    return "unknown", {}


def export_programs_from_xlsx(xlsx_path: Path, output_path: Path) -> list[dict]:
    rows = _read_xlsx(xlsx_path)
    header = rows[0]
    programs: list[dict] = []
    for row in rows[1:]:
        if not any(row):
            continue
        data = {header[i]: (row[i] if i < len(row) else "") for i in range(len(header)) if header[i]}
        url = data.get("Конкурсный список 2026", "")
        parser, parser_meta = _detect_parser(url)
        program_text = data.get("Образовательная программа", "").strip()
        code = data.get("Уод направления подготовки") or data.get("Код направления подготовки") or ""
        programs.append(
            {
                "id": len(programs) + 1,
                "university": data.get("ВУЗ", ""),
                "direction_code": str(code).replace(".0", ""),
                "competition_group": data.get("Название конкурсной группы / факультета", ""),
                "program": program_text,
                "passing_score_2025": float(data.get("Проходной балл в 2025г бюджет") or 0),
                "dima_score": int(float(data.get("Балл Димы с учетом ИД") or 0)),
                "budget_places": int(float(data.get("Кол-во мест 2026 основной конкурс") or 0)),
                "study_plan_url": data.get("Учебный план", ""),
                "list_url": url,
                "parser": parser,
                "parser_meta": parser_meta,
                "needs_filter": ("1 список" in program_text) or ("2 разные" in program_text),
            }
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(programs, ensure_ascii=False, indent=2), encoding="utf-8")
    return programs
