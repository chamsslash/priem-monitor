"""Авто-сверка результатов робота с живыми данными сайта.

Две независимые оси (см. брейншторм 2026-08-03):

1. Места (ВХОД робота) — провенанс числа мест: снято ли оно живьём с сайта
   (СТАНКИН kcp.php / МЭИ таблица КЦП / МИРЭА plan из API) или взято из
   аварийного резерва (протухший хардкод). Это ловит ту боль, что кусала дважды:
   тихой подмены мест устаревшими числами больше не будет — сверка подсветит
   «из резерва». Подробный разбор чисел — в `seat_oracle.py`.

2. Прогноз (ВЫХОД робота) — сверка предсказанного роботом зачисления Димы с
   фактическими проходными баллами среди СОГЛАСНЫХ на сайте. Робот моделирует
   Диму так, будто он прямо сейчас подал согласие и стоит в консент-пуле;
   оракул — реальный проходной балл среди согласных по каждому направлению
   (мин. балл среди тех, кого сайт туда зачислил). Идём по приоритетам робота и
   берём первое направление, где балл Димы этот порог перебивает — и сравниваем
   с прогнозом. Сверка честна ровно пока вердикт сайта о ЛЮДЯХ не участвует во
   входе каскада: места берутся из официального КЦП, а кто куда попадёт, робот
   выводит сам.
"""

from __future__ import annotations

import logging

from .models import (
    PlacementCheck,
    RobotPerson,
    RobotProgram,
    RobotSimulationResult,
    SeatCheck,
    VerificationReport,
)

logger = logging.getLogger(__name__)

# Вузы, где сверка прогноза честная (независимый оракул сайта).
# СТАНКИН — колонка «Высший проходной приоритет» в конкурсном списке;
# МИРЭА — minScore бюджетного конкурса из competitions_api;
# МЭИ — колонка «Высший проходной» в списках /inform/list<N>bacc.html;
# ФА — колонка «Высший проходной приоритет» в listabit.php.
# Общее условие: вердикт сайта о людях робот НЕ читает на входе — он берёт
# только места (официальный КЦП) и строки списков, а кто куда попадёт, выводит
# своим каскадом. Пока МЭИ брал места из «вакантных мест» волны и вычёркивал
# помеченных «Зачисляется в другой КГ», сверка была бы круговой: и число мест,
# и состав конкурентов приходили из уже готового ответа сайта.
# ФА: места берутся из приказа о КЦП (PDF на fa.ru) — 40 из 45 программ. Пять
# оставшихся сидят на заглушке не по недосмотру: их нет в московской очной
# секции приказа, они существуют только в филиалах, и после фильтра по кампусу
# у них ноль абитуриентов — на каскад заглушка не влияет.
# Московский политех — колонка «Высший проходной приоритет» в ответе
# fio_list_curl.php, места — «Остаток на общий конкурс» из шапки того же
# ответа; вердикт сайта о людях на вход каскада не идёт.
PLACEMENT_VERIFIED_UNIVERSITIES = {
    "СТАНКИН",
    "МИРЭА",
    "МЭИ",
    "Финансовый университет",
    "Московский политех",
}


def _title_for_key(programs: list[RobotProgram], key: str | None) -> str | None:
    if key is None:
        return None
    return next((program.title for program in programs if program.key == key), None)


def _priority_keys(person: RobotPerson | None) -> list[str]:
    """Ключи направлений в порядке приоритета (без дублей).

    Раньше шла по person.choices напрямую, без фильтра enrolls_elsewhere —
    в отличие от RobotPerson.ordered_program_keys(), по которому считает
    каскад. Из-за этого _site_placement мог поселить Диму на направление,
    откуда сайт его уже вычеркнул пометкой «Исключен (зачислен на другой
    конкурс)»: choice остаётся в person.choices ради отображения (см.
    комментарий у ProgramChoice.enrolls_elsewhere), но каскад туда не
    сажает — оракул сверки не должен туда селить тоже. Учёт пометки здесь,
    в СВЕРКЕ (не на входе каскада), круговизны не создаёт — она и есть
    вердикт сайта о человеке, которым сверка и оперирует."""
    if person is None:
        return []
    return person.ordered_program_keys()


def _build_seat_checks(
    result: RobotSimulationResult, programs: list[RobotProgram]
) -> list[SeatCheck]:
    by_key = {program.key: program for program in programs}
    checks: list[SeatCheck] = []
    # user_programs — реальные выборы человека из конкурсного списка, а не
    # захардкоженное подмножество конфига (см. models.RobotSimulationResult).
    for state in result.user_programs:
        program = by_key.get(state.program_key)
        checks.append(
            SeatCheck(
                program_key=state.program_key,
                title=state.title,
                tracked_id=state.tracked_id,
                budget_places=state.budget_places,
                seat_source=program.seat_source if program else None,
            )
        )
    return checks


def _oracle_cutoff(program: RobotProgram) -> int | None:
    """Проходной балл направления, если оракулом сайта вообще можно пользоваться.

    Наличия проходного балла НЕДОСТАТОЧНО. У СТАНКИНа и Политеха колонка
    «Высший проходной приоритет» на странице есть и разбирается (у всех строк
    значение «нет», а не отсутствие колонки), но отмеченных в ней НЕТ НИ ОДНОГО:
    приказы о зачислении уже вышли, и вуз свой оракул снял. `_passing_cutoff` в
    этом случае отдаёт 0, и условие «балл >= 0» истинно всегда — оракул
    вырождается в константу «первый приоритет» и выдаёт выдуманный вердикт: у
    Политеха ложное «совпало», у СТАНКИНа ложное «расхождение».

    `site_passing_count` считается всеми пулами ровно для этого случая (см. его
    докстроку рядом с `_passing_cutoff`), но до сих пор ни разу не читался.
    Ноль означает «отметок о проходящих на странице нет» — сверять не с чем.
    Отличить «оракул сняли после приказов» от «ещё не выставили» по данным
    нельзя, да и не нужно: в обоих случаях сверка невозможна.

    None — не то же самое, что 0, и его тут быть не должно: у МИРЭА порог берётся
    из `minScore` competitions_api, отмеченных людей в этом источнике нет по
    устройству, а сам порог настоящий. Поэтому проверка именно на `== 0`.
    """
    if program.passing_cutoff is None or program.site_passing_count == 0:
        return None
    return program.passing_cutoff


def _published_placement(person: RobotPerson | None) -> tuple[bool, str | None]:
    """Опубликованный вузом результат зачисления этого человека.

    Возвращает (результат опубликован, куда зачислен). Когда вуз назвал
    зачисленных поимённо (`ProgramChoice.enrolled`), сравнивать прогноз с
    проходным баллом незачем и вредно: порог — это оценка, а список зачисленных
    — факт. У МИРЭА факт и порог прямо разошлись (см. `_enrolled_here`), и прав
    оказался прогноз робота, а не порог.

    «Опубликован» определяется по наличию хотя бы одной отметки во ВСЕЙ выборке
    человека, а не по конкретному направлению: отсутствие отметки у человека
    означает «не зачислен», и это полноценный ответ, а не пробел в данных.
    Признак того, что вуз вообще перешёл к публикации результатов, приходит
    снаружи — см. вызов в `_build_placement_check`.
    """
    if person is None:
        return False, None
    # Отметка на человеке (Политех — приказ о зачислении) главнее отметки на
    # выборе: вуз убирает зачисленного из списка той программы, куда он поступил,
    # и подходящего ProgramChoice у него уже нет.
    if person.enrolled_key is not None:
        return True, person.enrolled_key or None
    for key in person.ordered_program_keys():
        choice = next((item for item in person.choices if item.program_key == key), None)
        if choice is not None and choice.enrolled:
            return True, key
    return False, None


def published_cutoffs(people: list[RobotPerson]) -> dict[str, int]:
    """Проходной балл по каждому направлению, выведенный из СПИСКА ЗАЧИСЛЕННЫХ.

    Это минимальный балл среди тех, кого вуз уже зачислил сюда. Отвечает не на
    вопрос «есть ли вы в списках» (согласие вы не подавали — и не окажетесь там
    никогда), а на тот, который человека и волнует: «попал бы я, если бы подал
    согласие». Балл выше или равный минимальному среди зачисленных и означает
    «прошёл бы».

    Порог из списка зачисленных лучше опубликованного вузом проходного балла
    тем, что это факт, а не оценка: он посчитан по людям, которых вуз реально
    зачислил. Хуже — тем, что он посчитан по ВИДИМОЙ части зачисленных. У
    Политеха вуз убирает зачисленного из списка того направления, куда он
    поступил, и человек виден только через списки своих остальных приоритетов;
    у кого приоритет был один, тот исчезает целиком. Невидимые обычно как раз
    внизу списка, поэтому порог получается не заниженным, а завышенным — то
    есть ошибается в безопасную сторону («не хватило» вместо «прошёл бы»).

    БВИ-шники в расчёт не идут: их зачисляют вне конкурса баллов, и их балл
    порогом не является — один олимпиадник со скромным ЕГЭ обвалил бы планку
    для всех.
    """
    scores: dict[str, list[int]] = {}
    for person in people:
        if person.is_bvi:
            continue
        published, key = _published_placement(person)
        if not published or key is None:
            continue
        scores.setdefault(key, []).append(person.score)
    return {key: min(values) for key, values in scores.items() if values}


def _results_published(people: list[RobotPerson]) -> bool:
    """Перешёл ли вуз к публикации поимённых результатов зачисления.

    Одной отметки по вузу достаточно: список зачисленных либо публикуется, либо
    нет. Проверяем по всей выборке, а не по одному человеку, иначе «этот не
    зачислен» было бы неотличимо от «вуз ещё ничего не публиковал».
    """
    return any(person.enrolled_key is not None for person in people) or any(
        choice.enrolled for person in people for choice in person.choices
    )


def _walk_priorities(
    cutoff_by_key: dict[str, int | None], sim_dima: RobotPerson | None, dima_score: int
) -> str | None:
    """Первое по приоритету направление, где балл Димы перебивает порог."""
    for key in _priority_keys(sim_dima):
        cutoff = cutoff_by_key.get(key)
        if cutoff is None:
            continue
        if dima_score >= cutoff:
            return key
    return None


def _site_placement(
    programs: list[RobotProgram], sim_dima: RobotPerson | None, dima_score: int
) -> str | None:
    """Куда сайт-порог селит Диму: первое по приоритету направление, где его балл
    перебивает реальный проходной среди согласных."""
    cutoff_by_key = {program.key: _oracle_cutoff(program) for program in programs}
    return _walk_priorities(cutoff_by_key, sim_dima, dima_score)


def _build_placement_check(
    university: str,
    result: RobotSimulationResult,
    programs: list[RobotProgram],
    sim_dima: RobotPerson | None,
    people: list[RobotPerson],
) -> PlacementCheck | None:
    if university not in PLACEMENT_VERIFIED_UNIVERSITIES:
        return None
    robot_key = result.dima_placed_program_key
    robot_title = result.dima_placed_title or _title_for_key(programs, robot_key)

    # Вуз опубликовал поимённые результаты — порог считаем по ним, а не по
    # колонке «Высший проходной приоритет»: список зачисленных это факт, а
    # колонку вуз после приказов обычно снимает.
    #
    # Сверять «есть ли Дима в списке зачисленных» бессмысленно: согласие он не
    # подавал, значит его там нет и быть не может, и любой прогноз «проходит»
    # автоматически считался бы ошибкой. Вопрос ставится так же, как везде в
    # роботе: как будто согласие подано. Отсюда порог — минимальный балл среди
    # зачисленных (см. published_cutoffs), а дальше обычный обход приоритетов.
    if _results_published(people):
        published: dict[str, int | None] = dict(published_cutoffs(people))
        site_key = _walk_priorities(published, sim_dima, result.dima_score)
        cutoff = published.get(site_key) if site_key is not None else None
        if robot_key == site_key:
            status = "match"
        else:
            status = "boundary" if cutoff == result.dima_score else "mismatch"
        return PlacementCheck(
            status=status,
            robot_key=robot_key,
            robot_title=robot_title,
            site_key=site_key,
            site_title=_title_for_key(programs, site_key),
            site_cutoff=cutoff,
            source="published",
        )

    cutoff_by_key = {program.key: _oracle_cutoff(program) for program in programs}

    # Нет ни одного пригодного проходного балла — колонки не было либо вуз ещё
    # не отметил ни одного проходящего (см. _oracle_cutoff). Сверять не с чем.
    if all(cutoff is None for cutoff in cutoff_by_key.values()):
        return PlacementCheck(
            status="unavailable",
            robot_key=robot_key,
            robot_title=robot_title,
            site_key=None,
            site_title=None,
        )

    # Направления теперь берутся из живого пула, а не только из конфига, и
    # часть из них в принципе без оракула (например, 5 филиальных программ ФА
    # без колонки «Высший проходной приоритет» — см. PLACEMENT_VERIFIED_UNIVERSITIES
    # выше), хотя у остальных направлений вуза проходные баллы есть. Без этой
    # проверки такое направление читалось бы как "mismatch" (робот селит сюда,
    # у сайта порог известен где-то ещё), хотя сверять именно тут просто нечем.
    if robot_key is not None and cutoff_by_key.get(robot_key) is None:
        return PlacementCheck(
            status="unavailable",
            robot_key=robot_key,
            robot_title=robot_title,
            site_key=None,
            site_title=None,
        )

    site_key = _site_placement(programs, sim_dima, result.dima_score)
    if robot_key == site_key:
        status = "match"
    else:
        # Граница, а не расхождение: проходной балл — это минимальный балл среди
        # прошедших, и при РАВНОМ балле проходит не каждый (дальше решают ИД,
        # преимущественное право, баллы по предметам — правил тай-брейка у нас
        # нет). Оракул с его «балл >= порога» на этой границе всегда оптимистичен,
        # а каскад — как повезёт с порядком. Спорить тут не о чем: помечаем
        # отдельно, чтобы не считать это ошибкой модели и не прятать факт.
        at_boundary = site_key is not None and cutoff_by_key.get(site_key) == result.dima_score
        status = "boundary" if at_boundary else "mismatch"
    return PlacementCheck(
        status=status,
        robot_key=robot_key,
        robot_title=robot_title,
        site_key=site_key,
        site_title=_title_for_key(programs, site_key),
        site_cutoff=cutoff_by_key.get(site_key) if site_key is not None else None,
    )


def _log_report(report: VerificationReport) -> None:
    fallback = report.fallback_seats
    if fallback:
        details = ", ".join(
            f"{check.title}={check.budget_places}({check.seat_source})" for check in fallback
        )
        logger.warning("[сверка %s] места из резерва: %s", report.university, details)
    placement = report.placement
    if placement is not None and placement.status in ("mismatch", "boundary"):
        oracle = "порог по зачисленным" if placement.source == "published" else "сайт-порог"
        logger.warning(
            "[сверка %s] прогноз (%s): робот → %s, %s → %s",
            report.university,
            placement.status,
            placement.robot_title or "не проходит",
            oracle,
            placement.site_title or "не проходит",
        )
    if not fallback and (placement is None or placement.status not in ("mismatch", "boundary")):
        logger.info(
            "[сверка %s] места живьём: %s; прогноз: %s",
            report.university,
            report.all_seats_live,
            placement.status if placement else "n/a",
        )


def build_verification_report(
    university: str,
    result: RobotSimulationResult,
    programs: list[RobotProgram],
    *,
    sim_dima: RobotPerson | None,
    people: list[RobotPerson] | None = None,
) -> VerificationReport:
    report = VerificationReport(
        university=university,
        seats=_build_seat_checks(result, programs),
        placement=_build_placement_check(university, result, programs, sim_dima, people or []),
    )
    _log_report(report)
    return report
