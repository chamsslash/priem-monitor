from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ProgramChoice:
    program_key: str
    priority: int
    is_bvi: bool = False
    # Сайт вуза уже определил, что этот человек зачислится по ДРУГОЙ
    # конкурсной группе (пометка «Зачисляется в другой КГ»), несмотря на
    # присутствие в этом списке. Выбор сохраняется (чтобы Димины реальные
    # приоритеты не пропадали из отображения), но каскад не должен пытаться
    # занять им место здесь — это место уже учтено как свободное в живом
    # budget_places с сайта.
    enrolls_elsewhere: bool = False


@dataclass
class RobotPerson:
    code: str
    score: int
    consent: bool
    is_bvi: bool = False
    choices: list[ProgramChoice] = field(default_factory=list)

    def ordered_program_keys(self, *, phase: str | None = None) -> list[str]:
        ordered = sorted(self.choices, key=lambda item: item.priority)
        seen: set[str] = set()
        result: list[str] = []
        for choice in ordered:
            if choice.program_key in seen:
                continue
            if choice.enrolls_elsewhere:
                continue
            if phase == "bvi" and not choice.is_bvi:
                continue
            seen.add(choice.program_key)
            result.append(choice.program_key)
        return result

    def has_bvi_choice(self) -> bool:
        return any(choice.is_bvi for choice in self.choices)


@dataclass
class RobotProgram:
    key: str
    title: str
    budget_places: int | None
    tracked_id: int | None = None


@dataclass
class ProgramState:
    program_key: str
    title: str
    budget_places: int | None
    remaining: int | None
    tracked_id: int | None = None
    bvi_enrolled: int = 0
    exam_enrolled: int = 0
    enrolled: list[str] = field(default_factory=list)


@dataclass
class P1CompetitorHigherPriority:
    priority: int
    program_key: str
    title: str
    passing_score: int | None = None


@dataclass
class CompetitorBeforeDima:
    code: str
    score: int
    consent: bool
    priority_on_program: int
    phase: str
    top_choice_consent: bool
    higher_priorities: list[P1CompetitorHigherPriority] = field(default_factory=list)


@dataclass
class DimaPrioritySnapshot:
    priority: int
    program_key: str
    title: str
    budget_places: int | None
    remaining_at_turn: int | None
    tracked_id: int | None = None

    @property
    def can_enter(self) -> bool:
        return self.remaining_at_turn is not None and self.remaining_at_turn > 0


@dataclass
class RobotSimulationResult:
    university: str
    total_people: int
    bvi_people: int
    exam_people: int
    directions_total: int
    dima_code: str
    dima_score: int
    dima_exam_queue_rank: int | None
    dima_people_before: int
    dima_ahead_in_exam: int
    dima_remaining_at_turn: list[DimaPrioritySnapshot]
    dima_placed_program_key: str | None
    dima_placed_title: str | None
    dima_priority_used: int | None
    dima_placed_via: str | None
    programs: list[ProgramState]
    tracked_programs: list[ProgramState]
    require_consent: bool
    dima_competitors_by_program: dict[str, list[CompetitorBeforeDima]] = field(default_factory=dict)
    from_cache: bool = False
    fetched_at: str | None = None
    error: str | None = None
