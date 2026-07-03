from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Applicant:
    score: int
    consent: bool
    priority: int
    is_virtual: bool = False


@dataclass
class ProgramConfig:
    id: int
    university: str
    direction_code: str
    competition_group: str
    program: str
    passing_score_2025: float
    dima_score: int
    budget_places: int
    study_plan_url: str
    list_url: str
    parser: str
    parser_meta: dict[str, Any] = field(default_factory=dict)
    needs_filter: bool = False
    filter_rules: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProgramResult:
    program: ProgramConfig
    applicants: list[Applicant]
    fetched_at: str
    error: Optional[str] = None
    source_places: Optional[int] = None


@dataclass
class RankSnapshot:
    rank_all: int
    rank_consent: Optional[int]
    rank_priority1: Optional[int]
    rank_consent_priority1: Optional[int]
    status: str
    probability: float
    probability_label: str
    cutoff_consent_p1: Optional[int]
    gap_to_cutoff: Optional[int]
    above_without_consent: int
    updated_at: str
