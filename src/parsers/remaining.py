from __future__ import annotations

from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from ..models import ProgramConfig, ProgramResult
from .base import BaseParser
from .utils import parse_table_by_headers


class StankinParser(BaseParser):
    name = "stankin"

    STANKIN_PROGRAMS = {
        "программная инженерия": "09.03.04 Программная инженерия",
        "разработка программных комплексов в рамках": "09.03.01 Информатика и вычислительная техника",
        "разработка программных комплексов": "09.03.01.01 Разработка программных комплексов",
        "математическое и компьютерное моделирование": "09.03.03.01 Математическое и компьютерное моделирование процессов и систем",
        "управление данными": "09.03.03.02 Управление данными",
        "цифровые системы управления": "09.03.02 Информационные системы и технологии",
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
