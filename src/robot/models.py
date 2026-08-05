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
    # Сколько человек сайт отметил проходящими сюда. Нижняя граница фактического
    # числа мест: сайт не может зачислить больше, чем есть. Единственный способ
    # поймать заниженное число мест там, где вуз не публикует КЦП машинно (ФА).
    site_passing_count: int | None = None
    # Сколько мест вуз РЕАЛЬНО выставил на текущую волну («Количество вакантных
    # мест» на странице волны МЭИ). Отличается от планового budget_places в обе
    # стороны: вверх — когда незаполненные квоты (особая, отдельная, целевая)
    # возвращаются в общий конкурс, вниз — когда часть мест уже занята прошлыми
    # приказами. Каскад это число НЕ берёт на вход: оно равно числу людей,
    # которых сайт сам отметил проходящими, и сверка выродилась бы в круг.
    # Живёт ради второго, оптимистичного прогноза и колонки «перенос квот».
    vacant_places: int | None = None
    # СКОЛЬКО КВОТНЫХ МЕСТ ОСТАЛОСЬ НЕЗАНЯТЫМИ — посчитано нами, а не прочитано
    # у вуза: официальный КЦП по видам квот минус фактически зачисленные по
    # квотным спискам. Незанятые квотные места вуз возвращает в общий конкурс,
    # поэтому budget_places + quota_shortfall — это НАШ независимый предикт
    # реальных мест. Тем он и ценен: vacant_places выше — тоже «реальные места»,
    # но это ответ сайта (их ровно столько, сколько людей он отметил
    # проходящими), а недобор выведен из данных, ответа не содержащих.
    quota_shortfall: int | None = None

    @property
    def predicted_places(self) -> int | None:
        """Места общего конкурса с учётом возвращённых квотных недоборов."""
        if self.budget_places is None or self.quota_shortfall is None:
            return None
        return self.budget_places + self.quota_shortfall


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
    # Ответ ОРАКУЛА по этому направлению: проходной балл среди согласных прямо с
    # сайта вуза. Робот его не использует в расчёте — он показывается рядом с
    # выводом робота, чтобы расхождение было видно сразу, а не пряталось.
    # 0 — сюда проходит любой (места открыты); None — вуз не публикует порог.
    site_cutoff: int | None = None

    @property
    def can_enter(self) -> bool:
        return self.remaining_at_turn is not None and self.remaining_at_turn > 0

    def site_lets_in(self, score: int) -> bool | None:
        """Пускает ли сюда сайт по своему порогу. None — порога нет."""
        if self.site_cutoff is None:
            return None
        return score >= self.site_cutoff

    def is_tie(self, score: int) -> bool:
        """Балл РОВНО равен проходному — исход тут не определён никакой моделью.

        Проходной балл сайта — это минимальный балл среди прошедших, поэтому при
        равенстве часть таких абитуриентов проходит, а часть нет: спор решают
        баллы по профильным предметам и преимущественное право. Считать это
        ошибкой робота неверно, молчать — тоже: человеку важно знать, что он
        стоит ровно на грани.
        """
        return self.site_cutoff is not None and self.site_cutoff == score

    def agrees_with_site(self, score: int) -> bool | None:
        """Сходятся ли робот и сайт. None — сверять не с чем либо ничья."""
        site = self.site_lets_in(score)
        if site is None or self.is_tie(score):
            return None
        return site == self.can_enter


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
class QuotaTransfer:
    """Насколько мест на волне больше (или меньше) планового общего конкурса.

    Положительная дельта — вуз вернул в общий конкурс незаполненные квоты
    (особую, отдельную, целевую): желающих на квотные места не хватило, и они
    достались всем. Отрицательная — часть мест уже занята прошлыми приказами.
    """

    program_key: str
    title: str
    planned: int
    actual: int
    # Сколько мест сайт реально выставил на волну. Справочно, рядом с нашим
    # предиктом: если они сильно расходятся, предикт стоит перепроверить.
    # None — вуз этого числа не публикует.
    site_actual: int | None = None

    @property
    def delta(self) -> int:
        return self.actual - self.planned


@dataclass
class OptimisticOutlook:
    """Второй прогноз: каскад на РЕАЛЬНО выставленных на волну местах.

    Основной прогноз считает по плановому КЦП общего конкурса — он проверяем
    и не зависит от вердикта сайта о людях. Этот считает по «Количеству
    вакантных мест» волны, где незаполненные квоты уже переехали в общий
    конкурс. Он ближе к сегодняшней реальности, но НЕ является более точной
    моделью: число вакантных мест равно числу людей, которых сайт сам отметил
    проходящими, поэтому совпадение с сайтом здесь самоподтверждающееся.
    Показываем как «оптимистичный сценарий», а не как уточнение.
    """

    transfers: list[QuotaTransfer]
    placed_program_key: str | None
    placed_title: str | None
    priority_used: int | None
    # Куда селит оракул сайта. Второй каскад считает на местах, приближенных к
    # реальным, поэтому именно ЕГО вердикт и должен сходиться с сайтом — по
    # нему и видно, попали мы или нет.
    site_key: str | None = None
    # Вердикты разошлись только из-за ничьей: балл ровно равен проходному, и
    # исход там не определён. Считать это несовпадением моделей неверно.
    tied: bool = False

    @property
    def gained_places(self) -> int:
        return sum(item.delta for item in self.transfers if item.delta > 0)

    @property
    def matches_site(self) -> bool:
        return self.placed_program_key == self.site_key or self.tied


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
    # Второй прогноз на реально выставленных на волну местах. None — вуз не
    # публикует вакантные места по волне (все, кроме МЭИ) либо они совпали с
    # плановыми, и второму сценарию нечего показать.
    optimistic: OptimisticOutlook | None = None
