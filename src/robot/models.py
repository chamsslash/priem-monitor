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
    # Откуда взято budget_places (провенанс). Нужно, чтобы сверка мест
    # отличала живое число от аварийного резерва:
    #   "live"     — официальное число мест общего конкурса с сайта
    #                (СТАНКИН kcp.php / МЭИ таблица КЦП / МИРЭА plan из API)
    #   "fallback" — захардкоженный резерв (STANKIN_KCP_OVERRIDES) — сайт не ответил
    #   "nap"      — nap-страница СТАНКИНа (полный КЦП, завышает)
    #   "config"   — число из config/programs.json
    #   "approx"   — грубая аппроксимация для непрофильных untracked-направлений
    seat_source: str | None = None
    # Проходной балл среди СОГЛАСНЫХ на это направление: мин. балл среди тех, у кого
    # колонка «Высший проходной приоритет» = ✓ (фактический набор зачисленных сайтом
    # среди подавших согласие). Оракул для сверки прогноза «как будто Дима подал
    # согласие»: он проходит на своё высшее направление, где балл >= этого порога.
    # 0 — среди согласных сюда никто не проходит (места открыты); None — колонки нет.
    passing_cutoff: int | None = None


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

    # Сверка «как будто Дима подал согласие»: робот ставит его в консент-пул,
    # оракул — фактические проходные баллы среди согласных с сайта.
    # status:
    #   "match"        — робот и сайт-порог селят Диму на одно направление
    #   "boundary"     — расходятся, но балл РАВЕН проходному у направления сайта:
    #                    при равном балле проходит не каждый, и решить спор нечем
    #   "mismatch"     — расходятся по существу (робот → одно, сайт-порог → другое/никуда)
    #   "unavailable"  — нет данных проходных баллов (колонки нет / другая стадия)
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
    # True — error вызван настройкой (вуз не поддерживается / выключен в
    # config/robot.json / нет отслеживаемых программ), а не состоянием пула.
    # Пул в таких случаях вообще не читался, поэтому fetched_at=None — но это
    # НЕ значит «кэш протух», и не должно заказывать догрев у refresh_worker.
    config_error: bool = False
    verification: VerificationReport | None = None
