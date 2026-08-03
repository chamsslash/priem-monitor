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
    # Вердикт САЙТА по этому человеку на этом направлении: колонка «Высший
    # проходной приоритет» (PROPERTY_710, пока только СТАНКИН). True — сайт считает,
    # что он реально зачисляется сюда по высшему приоритету С УЧЁТОМ согласия;
    # False — не проходит (или согласие не подано → пусто); None — колонки нет
    # (другой вуз/стадия). Робот эту величину НЕ использует в каскаде — она
    # нужна только как независимый оракул для сверки прогноза (см. verification).
    site_passes_here: bool | None = None


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
    # Откуда взято budget_places (провенанс). Нужно, чтобы сверка мест
    # отличала живое число от аварийного резерва:
    #   "live"     — снято живьём с сайта (СТАНКИН kcp.php / МЭИ вакантные места)
    #   "fallback" — захардкоженный резерв (STANKIN_KCP_OVERRIDES) — сайт не ответил
    #   "nap"      — nap-страница СТАНКИНа (полный КЦП, завышает)
    #   "config"   — число из config/programs.json
    #   "approx"   — грубая аппроксимация для непрофильных untracked-направлений
    seat_source: str | None = None


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
class SeatCheck:
    """Сверка числа мест по одному направлению: живьём или из резерва."""

    program_key: str
    title: str
    tracked_id: int | None
    budget_places: int | None
    seat_source: str | None

    @property
    def is_live(self) -> bool:
        return self.seat_source == "live"


@dataclass
class PlacementCheck:
    """Сверка прогноза робота с вердиктом самого сайта по Диме (СТАНКИН)."""

    # status:
    #   "match"        — робот и сайт указывают одно направление
    #   "mismatch"     — расходятся (робот → одно, сайт → другое/никуда)
    #   "unavailable"  — сайт сейчас не даёт вердикта (колонки/маркера нет)
    #   "no_consent"   — согласие не подано: сайт считает «проходной» только по
    #                    подавшим согласие, робот моделирует зачисление среди них —
    #                    без согласия сверять нечего (для Димы сейчас именно это)
    #   "hypothetical" — робот считает по ЗАДАННЫМ приоритетам, а не по реально
    #                    поданным Димой; сайт считает по реальным → не сопоставимо,
    #                    строгую сверку не проводим (только когда приоритеты совпали)
    status: str
    robot_key: str | None
    robot_title: str | None
    site_key: str | None
    site_title: str | None


@dataclass
class VerificationReport:
    university: str
    seats: list[SeatCheck] = field(default_factory=list)
    placement: PlacementCheck | None = None

    @property
    def fallback_seats(self) -> list[SeatCheck]:
        return [check for check in self.seats if not check.is_live]

    @property
    def all_seats_live(self) -> bool:
        return bool(self.seats) and all(check.is_live for check in self.seats)


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
    verification: VerificationReport | None = None
