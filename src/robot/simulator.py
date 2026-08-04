from __future__ import annotations

from ..config_loader import load_programs
from ..models import ProgramConfig
from .config import RobotSettings, load_robot_config
from .direction_keys import direction_key_for_program
from .priorities import program_display_name
from .models import (
    CompetitorBeforeDima,
    DimaPrioritySnapshot,
    P1CompetitorHigherPriority,
    ProgramChoice,
    ProgramState,
    RobotPerson,
    RobotProgram,
    RobotSimulationResult,
)
from .universities import SUPPORTED_UNIVERSITIES, fetch_university_pool, read_cached_pool
from .verification import build_verification_report


def _find_dima_in_pool(people: list[RobotPerson], list_code: str) -> RobotPerson | None:
    for person in people:
        if str(person.code) == str(list_code):
            return person
    return None


def _filter_choices_to_tracked(
    dima: RobotPerson,
    tracked_programs: list[ProgramConfig],
    *,
    consent: bool,
) -> RobotPerson:
    tracked_keys = {direction_key_for_program(program) for program in tracked_programs}
    choices = [choice for choice in dima.choices if choice.program_key in tracked_keys]
    if not choices:
        return RobotPerson(
            code=dima.code,
            score=dima.score,
            consent=consent,
            is_bvi=dima.is_bvi,
            choices=dima.choices,
        )
    return RobotPerson(
        code=dima.code,
        score=dima.score,
        consent=consent,
        is_bvi=dima.is_bvi,
        choices=choices,
    )


def _build_dima_person(
    settings: RobotSettings,
    university_cfg: dict,
    tracked_programs: list[ProgramConfig],
    *,
    priority_ids: list[int] | None = None,
) -> RobotPerson:
    if priority_ids is not None:
        selected_ids = [int(item) for item in priority_ids]
    else:
        selected_ids = [int(item) for item in university_cfg.get("dima_priorities", [])]

    tracked_by_id = {program.id: program for program in tracked_programs}
    choices: list[ProgramChoice] = []
    for index, program_id in enumerate(selected_ids):
        program = tracked_by_id.get(program_id)
        if program is None:
            continue
        choices.append(
            ProgramChoice(program_key=direction_key_for_program(program), priority=index + 1)
        )
    if not choices:
        raise ValueError("Не заданы приоритеты: укажите dima_priorities в config/robot.json или через --priorities")
    return RobotPerson(
        code=settings.dima_code,
        score=settings.dima_score,
        consent=settings.dima_consent,
        is_bvi=False,
        choices=choices,
    )


def _apply_priority_order(
    dima: RobotPerson,
    tracked_programs: list[ProgramConfig],
    priority_ids: list[int],
    *,
    require_in_pool: bool = True,
) -> RobotPerson:
    if not priority_ids:
        return dima

    tracked_by_id = {program.id: program for program in tracked_programs}
    pool_keys = {choice.program_key for choice in dima.choices}
    new_choices: list[ProgramChoice] = []

    for index, program_id in enumerate(priority_ids):
        program = tracked_by_id.get(program_id)
        if program is None:
            continue
        program_key = direction_key_for_program(program)
        if require_in_pool and program_key not in pool_keys:
            continue
        original = next((choice for choice in dima.choices if choice.program_key == program_key), None)
        new_choices.append(
            ProgramChoice(
                program_key=program_key,
                priority=index + 1,
                is_bvi=original.is_bvi if original is not None else False,
            )
        )

    if not new_choices:
        return dima

    return RobotPerson(
        code=dima.code,
        score=dima.score,
        consent=dima.consent,
        is_bvi=any(choice.is_bvi for choice in new_choices),
        choices=new_choices,
    )


def _resolve_dima_person(
    settings: RobotSettings,
    university_cfg: dict,
    tracked_programs: list[ProgramConfig],
    people: list[RobotPerson],
    *,
    priority_ids: list[int] | None = None,
) -> tuple[RobotPerson, bool]:
    list_code = university_cfg.get("dima_list_code")
    if list_code:
        found = _find_dima_in_pool(people, str(list_code))
        if found is None:
            raise ValueError(f"Дима ({list_code}) не найден в списках вуза")
        dima = _filter_choices_to_tracked(found, tracked_programs, consent=settings.dima_consent)
        saved_ids = priority_ids
        if saved_ids is None:
            saved_ids = [int(item) for item in university_cfg.get("dima_priorities", [])]
        if saved_ids:
            # require_in_pool=False: заявка Димы на направление реальна и известна из
            # конфига независимо от того, попала ли его строка в пул конкурентов — если
            # её исключили как «Зачисляется в другой КГ» (он уже проходит выше), это не
            # значит, что он сюда не подавал, и это направление всё равно нужно показать.
            dima = _apply_priority_order(dima, tracked_programs, saved_ids, require_in_pool=False)
        return dima, True
    return _build_dima_person(settings, university_cfg, tracked_programs, priority_ids=priority_ids), False


def _priority_on_program(person: RobotPerson, program_key: str) -> int:
    values = [choice.priority for choice in person.choices if choice.program_key == program_key]
    return min(values) if values else 99


def _try_place(
    person: RobotPerson,
    states: dict[str, ProgramState],
    *,
    phase: str,
) -> tuple[str | None, int | None]:
    for program_key in person.ordered_program_keys(phase=phase):
        state = states.get(program_key)
        if state is None or state.remaining is None or state.remaining <= 0:
            continue
        state.remaining -= 1
        state.enrolled.append(person.code)
        if phase == "bvi":
            state.bvi_enrolled += 1
        else:
            state.exam_enrolled += 1
        return program_key, _priority_on_program(person, program_key)
    return None, None


def _dima_remaining_snapshot(
    dima: RobotPerson,
    states: dict[str, ProgramState],
) -> list[DimaPrioritySnapshot]:
    ordered_choices = sorted(dima.choices, key=lambda item: item.priority)
    seen: set[str] = set()
    snapshots: list[DimaPrioritySnapshot] = []
    for choice in ordered_choices:
        if choice.program_key in seen:
            continue
        seen.add(choice.program_key)
        state = states.get(choice.program_key)
        if state is None:
            continue
        snapshots.append(
            DimaPrioritySnapshot(
                priority=choice.priority,
                program_key=choice.program_key,
                title=state.title,
                budget_places=state.budget_places,
                remaining_at_turn=state.remaining,
                tracked_id=state.tracked_id,
            )
        )
    return snapshots


def _passing_score_for_program(state: ProgramState, people_by_code: dict[str, RobotPerson]) -> int | None:
    if state.remaining is None or state.remaining > 0:
        return None
    scores = [people_by_code[code].score for code in state.enrolled if code in people_by_code and people_by_code[code].score > 0]
    return min(scores) if scores else None


def _build_competitor(
    person: RobotPerson,
    *,
    program_key: str,
    phase: str,
    states: dict[str, ProgramState],
    people_by_code: dict[str, RobotPerson],
) -> CompetitorBeforeDima:
    priority_on_program = _priority_on_program(person, program_key)
    higher_priorities: list[P1CompetitorHigherPriority] = []
    seen_higher: set[str] = set()
    for choice in sorted(person.choices, key=lambda item: item.priority):
        if choice.priority >= priority_on_program:
            break
        if choice.program_key in seen_higher:
            continue
        seen_higher.add(choice.program_key)
        state = states.get(choice.program_key)
        if state is None:
            continue
        higher_priorities.append(
            P1CompetitorHigherPriority(
                priority=choice.priority,
                program_key=choice.program_key,
                title=state.title,
                passing_score=_passing_score_for_program(state, people_by_code),
            )
        )
    return CompetitorBeforeDima(
        code=person.code,
        score=person.score,
        consent=person.consent,
        priority_on_program=priority_on_program,
        phase=phase,
        top_choice_consent=priority_on_program == 1 and person.consent,
        higher_priorities=higher_priorities,
    )


def _simulate_two_phase(
    university: str,
    programs: list[RobotProgram],
    people: list[RobotPerson],
    dima: RobotPerson,
    *,
    dima_in_pool: bool,
    require_consent: bool,
    from_cache: bool,
    fetched_at: str | None = None,
) -> RobotSimulationResult:
    states = {
        program.key: ProgramState(
            program_key=program.key,
            title=program.title,
            budget_places=program.budget_places,
            remaining=program.budget_places,
            tracked_id=program.tracked_id,
        )
        for program in programs
    }

    placed_codes: set[str] = set()
    dima_program_key: str | None = None
    dima_priority_used: int | None = None
    dima_placed_via: str | None = None
    dima_exam_queue_rank: int | None = None
    dima_people_before = 0
    dima_ahead_in_exam = 0
    dima_remaining_at_turn: list[DimaPrioritySnapshot] = []
    dima_program_keys = {choice.program_key for choice in dima.choices}
    dima_competitors_by_program: dict[str, list[CompetitorBeforeDima]] = {key: [] for key in dima_program_keys}

    people_by_code = {person.code: person for person in people}

    if dima_in_pool:
        participants = list(people)
    else:
        participants = [person for person in people if person.code != dima.code]
    if require_consent:
        participants = [person for person in participants if person.consent]

    bvi_queue = sorted([person for person in participants if person.has_bvi_choice()], key=lambda item: item.code)
    for person in bvi_queue:
        program_key, priority = _try_place(person, states, phase="bvi")
        if program_key is not None:
            placed_codes.add(person.code)
            if program_key in dima_competitors_by_program and person.code != dima.code:
                dima_competitors_by_program[program_key].append(
                    _build_competitor(
                        person,
                        program_key=program_key,
                        phase="bvi",
                        states=states,
                        people_by_code=people_by_code,
                    )
                )
            if person.code == dima.code:
                dima_program_key = program_key
                dima_priority_used = priority
                dima_placed_via = "bvi"

    exam_candidates = [person for person in participants if person.code not in placed_codes and person.score > 0]
    exam_queue = sorted(exam_candidates, key=lambda person: (-person.score, person.code))

    dima_eligible = (not require_consent or dima.consent) and dima.code not in placed_codes
    if not dima_in_pool and dima_eligible:
        exam_queue.append(dima)
        exam_queue.sort(key=lambda person: (-person.score, person.code))

    if dima_eligible and dima.code not in placed_codes:
        dima_exam_queue_rank = next(
            (index for index, person in enumerate(exam_queue, start=1) if person.code == dima.code),
            None,
        )
        if dima_exam_queue_rank is not None:
            dima_people_before = dima_exam_queue_rank - 1

    before_dima = True
    for person in exam_queue:
        if person.code in placed_codes:
            continue
        if person.code == dima.code:
            dima_remaining_at_turn = _dima_remaining_snapshot(dima, states)
            before_dima = False

        program_key, priority = _try_place(person, states, phase="exam")
        if program_key is None:
            continue
        placed_codes.add(person.code)
        if before_dima and program_key in dima_competitors_by_program and person.code != dima.code:
            dima_competitors_by_program[program_key].append(
                _build_competitor(
                    person,
                    program_key=program_key,
                    phase="exam",
                    states=states,
                    people_by_code=people_by_code,
                )
            )
        if person.code == dima.code:
            dima_program_key = program_key
            dima_priority_used = priority
            dima_placed_via = "exam"
        elif before_dima:
            dima_ahead_in_exam += 1

    dima_title = states[dima_program_key].title if dima_program_key else None
    tracked_states = sorted(
        [state for state in states.values() if state.tracked_id is not None],
        key=lambda item: item.tracked_id or 0,
    )
    if dima_in_pool:
        queue_size = len(participants)
        exam_people_count = len(exam_candidates)
    else:
        queue_size = len(participants) + (1 if dima_eligible else 0)
        exam_people_count = len(exam_candidates) + (1 if dima_eligible else 0)

    return RobotSimulationResult(
        university=university,
        total_people=queue_size,
        bvi_people=len(bvi_queue),
        exam_people=exam_people_count,
        directions_total=len(programs),
        dima_code=dima.code,
        dima_score=dima.score,
        dima_exam_queue_rank=dima_exam_queue_rank,
        dima_people_before=dima_people_before,
        dima_ahead_in_exam=dima_ahead_in_exam,
        dima_remaining_at_turn=dima_remaining_at_turn,
        dima_placed_program_key=dima_program_key,
        dima_placed_title=dima_title,
        dima_priority_used=dima_priority_used,
        dima_placed_via=dima_placed_via,
        dima_competitors_by_program=dima_competitors_by_program,
        programs=sorted(states.values(), key=lambda item: item.title),
        tracked_programs=tracked_states,
        require_consent=require_consent,
        from_cache=from_cache,
        fetched_at=fetched_at,
    )


def run_robot_simulation(
    university: str,
    settings: RobotSettings | None = None,
    *,
    use_cache: bool = True,
    stale_ok: bool = False,
    priority_ids: list[int] | None = None,
) -> RobotSimulationResult:
    """stale_ok=True — брать пул ТОЛЬКО из кэша, любого возраста, и никогда не
    ходить в сеть. Режим для обработчиков команд Telegram: они крутятся в том же
    потоке, что и опрос getUpdates, поэтому сетевая пересборка там подвесила бы
    бота для всех чатов сразу. Пересборку заказывает refresh_worker."""
    settings = settings or load_robot_config()
    parser_name = SUPPORTED_UNIVERSITIES.get(university)
    empty = RobotSimulationResult(
        university=university,
        total_people=0,
        bvi_people=0,
        exam_people=0,
        directions_total=0,
        dima_code=settings.dima_code,
        dima_score=settings.dima_score,
        dima_exam_queue_rank=None,
        dima_people_before=0,
        dima_ahead_in_exam=0,
        dima_remaining_at_turn=[],
        dima_placed_program_key=None,
        dima_placed_title=None,
        dima_priority_used=None,
        dima_placed_via=None,
        programs=[],
        tracked_programs=[],
        require_consent=settings.require_consent,
        from_cache=False,
    )

    if parser_name is None:
        empty.error = f"Робот пока не поддерживает вуз «{university}»"
        return empty

    university_cfg = settings.universities.get(university, {})
    if not university_cfg.get("enabled", True):
        empty.error = f"Симуляция для «{university}» отключена в config/robot.json"
        return empty

    tracked_programs = [
        program for program in load_programs() if program.university == university and program.parser == parser_name
    ]
    if not tracked_programs:
        empty.error = f"В конфиге нет программ для «{university}»"
        return empty

    try:
        if stale_ok:
            cached = read_cached_pool(parser_name)
            if cached is None:
                empty.error = "Данные ещё собираются, вернусь через пару минут"
                return empty
            people, programs, fetched_at, from_cache = cached
        else:
            people, programs, fetched_at, from_cache = fetch_university_pool(parser_name, use_cache=use_cache)
    except Exception as exc:  # noqa: BLE001
        empty.error = str(exc)
        return empty

    try:
        dima, dima_in_pool = _resolve_dima_person(
            settings,
            university_cfg,
            tracked_programs,
            people,
            priority_ids=priority_ids,
        )
    except ValueError as exc:
        empty.error = str(exc)
        return empty

    if dima_in_pool:
        people = [
            RobotPerson(
                code=person.code,
                score=person.score,
                consent=dima.consent if person.code == dima.code else person.consent,
                is_bvi=dima.is_bvi if person.code == dima.code else person.is_bvi,
                choices=dima.choices if person.code == dima.code else person.choices,
            )
            for person in people
        ]

    tracked_for_report = [
        program
        for program in tracked_programs
        if direction_key_for_program(program) in set(dima.ordered_program_keys())
    ]
    result = _simulate_two_phase(
        university,
        programs,
        people,
        dima,
        dima_in_pool=dima_in_pool,
        require_consent=settings.require_consent,
        from_cache=from_cache,
        fetched_at=fetched_at,
    )
    result.tracked_programs = [
        state
        for state in result.tracked_programs
        if state.tracked_id is not None
        and any(
            direction_key_for_program(program) == state.program_key
            for program in tracked_for_report
        )
    ]
    display_titles = {direction_key_for_program(program): program_display_name(program) for program in tracked_for_report}
    for state in result.tracked_programs:
        if state.program_key in display_titles:
            state.title = display_titles[state.program_key]
    for snapshot in result.dima_remaining_at_turn:
        if snapshot.program_key in display_titles:
            snapshot.title = display_titles[snapshot.program_key]
    if result.dima_placed_program_key in display_titles:
        result.dima_placed_title = display_titles[result.dima_placed_program_key]

    result.verification = build_verification_report(university, result, programs, sim_dima=dima)
    return result
