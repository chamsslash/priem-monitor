from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from .models import Applicant, ProgramConfig, RankSnapshot


def _rank_with_virtual(applicants: Iterable[Applicant], dima_score: int) -> list[Applicant]:
    rows = [a for a in applicants if not a.is_virtual]
    virtual = Applicant(score=dima_score, consent=True, priority=1, is_virtual=True)
    rows.append(virtual)
    rows.sort(key=lambda item: (-item.score, item.is_virtual))
    return rows


def _position(rows: list[Applicant], predicate) -> int | None:
    filtered = [row for row in rows if predicate(row)]
    for index, row in enumerate(filtered, start=1):
        if row.is_virtual:
            return index
    return None


def _status(rank: int | None, places: int, buffer: int = 2) -> str:
    if rank is None:
        return "unknown"
    if rank <= places:
        return "green"
    if rank <= places + buffer:
        return "yellow"
    return "red"


def _estimate_probability(
    rank_consent: int | None,
    rank_consent_p1: int | None,
    places: int,
    dima_score: int,
    cutoff_consent_p1: int | None,
    above_without_consent: int,
    passing_score_2025: float,
) -> tuple[float, str]:
    if places <= 0:
        return 0.0, "нет данных о местах"

    score = 0.0
    if rank_consent_p1 is not None:
        if rank_consent_p1 <= places:
            score += 0.55
        elif rank_consent_p1 <= places + 3:
            score += 0.25
        else:
            margin = rank_consent_p1 - places
            score += max(0.0, 0.2 - margin * 0.03)

    if rank_consent is not None:
        if rank_consent <= places:
            score += 0.2
        elif rank_consent <= places + 5:
            score += 0.08

    if cutoff_consent_p1 is not None:
        gap = dima_score - cutoff_consent_p1
        if gap >= 5:
            score += 0.12
        elif gap >= 0:
            score += 0.06
        else:
            score += max(-0.15, gap * 0.02)

    if above_without_consent <= 3:
        score += 0.08
    elif above_without_consent <= 8:
        score += 0.04

    if passing_score_2025 and dima_score >= passing_score_2025:
        score += 0.05

    # До 25.07 списки ещё двигаются — оставляем запас неопределённости.
    score = min(score, 0.92)
    score = max(score, 0.03)

    if score >= 0.75:
        label = "высокая"
    elif score >= 0.5:
        label = "средняя"
    elif score >= 0.25:
        label = "низкая"
    else:
        label = "очень низкая"
    return round(score, 2), label


def build_rank_snapshot(program: ProgramConfig, applicants: list[Applicant]) -> RankSnapshot:
    rows = _rank_with_virtual(applicants, program.dima_score)
    rank_all = next((idx for idx, row in enumerate(rows, start=1) if row.is_virtual), len(rows))

    consent_rows = [row for row in rows if row.consent or row.is_virtual]
    consent_rows.sort(key=lambda item: (-item.score, item.is_virtual))
    rank_consent = _position(consent_rows, lambda row: row.consent or row.is_virtual)

    priority1_rows = [row for row in rows if row.priority == 1 or row.is_virtual]
    priority1_rows.sort(key=lambda item: (-item.score, item.is_virtual))
    rank_priority1 = _position(priority1_rows, lambda row: row.priority == 1 or row.is_virtual)

    consent_p1_rows = [row for row in rows if (row.consent and row.priority == 1) or row.is_virtual]
    consent_p1_rows.sort(key=lambda item: (-item.score, item.is_virtual))
    rank_consent_p1 = _position(consent_p1_rows, lambda row: (row.consent and row.priority == 1) or row.is_virtual)

    places = program.budget_places
    status = _status(rank_consent_p1, places)

    cutoff_consent_p1 = None
    if len(consent_p1_rows) >= places:
        cutoff_consent_p1 = consent_p1_rows[places - 1].score

    above_without_consent = sum(
        1 for row in rows if not row.is_virtual and row.score > program.dima_score and not row.consent
    )

    gap_to_cutoff = None
    if cutoff_consent_p1 is not None:
        gap_to_cutoff = program.dima_score - cutoff_consent_p1

    probability, probability_label = _estimate_probability(
        rank_consent=rank_consent,
        rank_consent_p1=rank_consent_p1,
        places=places,
        dima_score=program.dima_score,
        cutoff_consent_p1=cutoff_consent_p1,
        above_without_consent=above_without_consent,
        passing_score_2025=program.passing_score_2025,
    )

    return RankSnapshot(
        rank_all=rank_all,
        rank_consent=rank_consent,
        rank_priority1=rank_priority1,
        rank_consent_priority1=rank_consent_p1,
        status=status,
        probability=probability,
        probability_label=probability_label,
        cutoff_consent_p1=cutoff_consent_p1,
        gap_to_cutoff=gap_to_cutoff,
        above_without_consent=above_without_consent,
        updated_at=datetime.now(timezone.utc).isoformat(),
    )
