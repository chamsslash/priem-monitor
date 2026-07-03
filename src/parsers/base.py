from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Applicant, ProgramConfig, ProgramResult


class BaseParser(ABC):
    name: str = "base"

    @abstractmethod
    def fetch(self, program: ProgramConfig) -> ProgramResult:
        raise NotImplementedError

    @staticmethod
    def _normalize_yes(value: object) -> bool:
        text = str(value or "").strip().lower()
        return text in {"1", "true", "yes", "да", "y"}
