from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from ..config_loader import load_programs
from .direction_keys import okso_code_for_program
from .models import (
    CompetitorBeforeDima,
    DimaPrioritySnapshot,
    RobotSimulationResult,
    VerificationReport,
)


def _okso_by_tracked_id() -> dict[int, str]:
    return {
        program.id: code
        for program in load_programs()
        if (code := okso_code_for_program(program)) is not None
    }


def _format_fetched_at(iso: str | None) -> str | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        local = dt.astimezone(ZoneInfo("Europe/Moscow"))
        return local.strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return None


def _data_header(result: RobotSimulationResult) -> str:
    title = f"🤖 Симуляция робота — {result.university}"
    fetched = _format_fetched_at(result.fetched_at)
    if not fetched:
        return title
    if result.from_cache:
        # Протухший кэш всё равно показываем (иначе бот молчал бы минутами),
        # но честно говорим, что цифры старые и свежие уже едут.
        from .universities import SUPPORTED_UNIVERSITIES, expected_refresh_hint, is_pool_stale

        parser_name = SUPPORTED_UNIVERSITIES.get(result.university)
        if parser_name is not None and is_pool_stale(parser_name, result.fetched_at):
            # Срок конкретный, а не «подождите»: у вузов он отличается в полтора
            # порядка (МИРЭА 3 секунды, СТАНКИН почти 8 минут), и без цифры
            # человек не понимает, обновлять через минуту или через десять.
            return (
                f"{title}\n"
                f"⏳ Списки сейчас подтягиваются с сайта вуза — это {expected_refresh_hint(parser_name)}. "
                f"Пока показываю данные от {fetched}; повторите команду позже, чтобы увидеть свежие."
            )
        return f"{title} (кэш от {fetched})"
    return f"{title} (данные от {fetched})"


def format_robot_cache_refresh(
    university: str,
    *,
    fetched_at: str | None,
    people_count: int,
    programs_count: int,
) -> str:
    fetched = _format_fetched_at(fetched_at)
    time_note = f"от {fetched}" if fetched else "обновлён"
    return (
        f"✅ Кэш робота — {university}\n"
        f"Списки {time_note}\n"
        f"Направлений: {programs_count} · абитуриентов: {people_count}"
    )


def _format_competitor(item: CompetitorBeforeDima) -> list[str]:
    score_note = f", {item.score} б." if item.phase == "exam" and item.score > 0 else ""
    if item.phase == "bvi":
        score_note = ", БВИ"
    if item.top_choice_consent:
        return [f"• {item.code}{score_note} — пр.1 + согласие"]

    lines = [f"• {item.code}{score_note}, пр. на программу: {item.priority_on_program}"]
    if item.higher_priorities:
        higher_parts: list[str] = []
        for higher in item.higher_priorities:
            part = f"пр.{higher.priority} {higher.title}"
            if higher.passing_score is not None:
                part += f" (проходной {higher.passing_score})"
            higher_parts.append(part)
        lines.append(f"  выше: {', '.join(higher_parts)}")
    return lines


def _format_verification(report: VerificationReport | None) -> list[str]:
    if report is None:
        return []
    lines = ["", "— Сверка с сайтом —"]

    fallback = report.fallback_seats
    if not report.seats:
        pass
    elif not fallback:
        lines.append("Места: сверено вживую ✅")
    else:
        names = ", ".join(f"{check.title} ({check.budget_places})" for check in fallback)
        lines.append(f"⚠️ Места из резерва (сайт не ответил): {names}")

    # Сам вердикт оракула печатается СРАЗУ под вердиктом робота
    # (`_format_site_verdict`) — повторять его здесь незачем. Здесь остаётся
    # только то, чего там нет: случай, когда сверять вообще нечем.
    placement = report.placement
    if placement is not None and placement.status == "unavailable":
        lines.append("ℹ️ Прогноз: нет проходных баллов на сайте — не сверить")
    return lines


def _format_site_verdict(result: RobotSimulationResult) -> list[str]:
    """Вердикт оракула — сразу под вердиктом робота, а не внизу письма.

    Робот — модель, оракул — официальные проходные баллы вуза. Когда они
    расходятся, человеку нужно видеть оба ответа рядом и в одном месте, чтобы
    не пришлось сопоставлять их самому через полписьма.
    """
    report = result.verification
    placement = report.placement if report else None
    if placement is None or placement.status == "unavailable":
        return []
    site = placement.site_title or "никуда не проходит"
    if placement.status == "match":
        return ["  Оракул сайта: то же самое ✅"]
    if placement.status == "boundary":
        return [
            f"  Оракул сайта: {site} ⚖️ — но это НИЧЬЯ, а не расхождение",
            "  Ваш балл ровно равен проходному. При равенстве проходят не все: "
            "спор решают профильные предметы и преимущественное право, и исход "
            "не определён ни нашей моделью, ни порогом сайта",
        ]
    return [
        f"  ⚠️ Оракул сайта: {site}",
        "  Робот и официальные проходные баллы разошлись — верить стоит сайту",
    ]


def _format_oracle(item: DimaPrioritySnapshot, score: int) -> str:
    """Колонка «сайт»: что говорит официальный проходной балл по этому направлению.

    Показывается ВСЕГДА, а не только при расхождении. Робот — модель, и он может
    ошибаться; вердикт вуза о том, кто проходит среди согласных, взят прямо с его
    страницы и в расчёт робота не входит. Видеть их рядом важнее, чем видеть один
    красивый ответ: если они разошлись, решает сайт, а не мы.
    """
    if item.site_cutoff is None:
        return ""
    threshold = "любой балл" if item.site_cutoff == 0 else f"проходной {item.site_cutoff}"
    if item.is_tie(score):
        # Ничья, а не расхождение: балл ровно равен проходному, и исход тут не
        # определён — при равенстве проходит не каждый.
        return f" · сайт: {threshold} ⚖️ ВАШ БАЛЛ РОВНО НА ГРАНИ"
    mark = "✅" if item.site_lets_in(score) else "❌"
    flag = " ⚠️расхождение" if item.agrees_with_site(score) is False else ""
    return f" · сайт: {threshold} {mark}{flag}"


def _format_optimistic(result: RobotSimulationResult) -> list[str]:
    """Второй прогноз — с учётом квотных мест, которые вуз вернул в общий конкурс.

    Основной прогноз считает по плановому КЦП и потому пессимистичен: он
    исходит из того, что все квотные места будут заняты. Здесь мы считаем,
    сколько их осталось незанятыми, и добавляем в общий конкурс.

    Недобор выведен нами из официального КЦП и квотных списков, а не прочитан с
    волны — поэтому это именно прогноз, а не пересказ ответа сайта. Число волны
    показываем рядом, когда оно расходится с предиктом.
    """
    outlook = result.optimistic
    if outlook is None:
        return []

    lines = ["", "— Прогноз с учётом квотных недоборов —"]
    for item in outlook.transfers:
        check = ""
        if item.site_actual is not None and item.site_actual != item.actual:
            check = f", сайт показывает {item.site_actual}"
        lines.append(
            f"  {item.title}: план {item.planned} → с недобором {item.actual} "
            f"({item.delta:+d}{check})"
        )
    if not outlook.transfers:
        lines.append(
            "  Квоты на ваших приоритетах добраны полностью — "
            "мест строго столько, сколько в КЦП"
        )

    if outlook.placed_program_key is None:
        lines.append("→ И так не проходите ни по одному приоритету")
    else:
        lines.append(
            f"→ Зачислится: {outlook.placed_title or ''} "
            f"({outlook.priority_used}-й приоритет)"
        )
    # Именно этот каскад считает на местах, приближенных к реальным, поэтому
    # сходиться с оракулом должен он — по нему и судим, попали мы или нет.
    if outlook.tied and outlook.placed_program_key != outlook.site_key:
        lines.append("  Сходится с оракулом сайта с точностью до ничьей ⚖️")
    elif outlook.matches_site:
        lines.append("  Всё сошлось с оракулом сайта ✅")
    else:
        lines.append("  ⚠️ С оракулом сайта не сходится")
    if outlook.placed_program_key != result.dima_placed_program_key:
        lines.append("  (от основного прогноза отличается — там места считаются строго по КЦП)")
    return lines


def _format_error_response(title: str, error: str) -> str:
    if "не найден в списках вуза" in error:
        return f"{title}\n\nВаш код не найден в списках этого вуза."

    # POOL_NOT_READY_ERROR — не ошибка, а нормальное состояние прогрева
    # (первые минуты после рестарта бота), поэтому без префикса «Ошибка:».
    from .simulator import POOL_NOT_READY_ERROR

    if error == POOL_NOT_READY_ERROR:
        return f"{title}\n\n{error}"
    return f"{title}\n\nОшибка: {error}"


def format_robot_result(result: RobotSimulationResult) -> str:
    if result.error:
        return _format_error_response(f"🤖 Робот — {result.university}", result.error)

    lines = [
        _data_header(result),
        f"Направлений: {result.directions_total} (весь вуз) · в очереди: {result.total_people} "
        f"(БВИ {result.bvi_people} · ЕГЭ {result.exam_people})",
    ]
    if not result.require_consent:
        lines.insert(1, "⚠️ Режим без фильтра согласия")

    if result.dima_remaining_at_turn:
        lines.append("")
        rank = result.dima_exam_queue_rank or "?"
        lines.append(
            f"Когда очередь доходит до вас ({result.dima_score} б., "
            f"{rank}-е место в очереди ЕГЭ, перед вами {result.dima_people_before} чел., "
            f"из них зачислено {result.dima_ahead_in_exam}):"
        )
        lines.append(f"Учитываются приоритеты ({len(result.dima_remaining_at_turn)}):")
        okso_by_id = _okso_by_tracked_id()
        disagreements = 0
        ties = 0
        for item in result.dima_remaining_at_turn:
            if item.is_tie(result.dima_score):
                ties += 1
            okso = okso_by_id.get(item.tracked_id) if item.tracked_id is not None else None
            prefix = f"{okso} " if okso else ""
            suffix = f" (код {item.tracked_id})" if item.tracked_id is not None else ""
            oracle = _format_oracle(item, result.dima_score)
            if item.agrees_with_site(result.dima_score) is False:
                disagreements += 1
            if item.budget_places is None or item.remaining_at_turn is None:
                lines.append(f"  {item.priority}. {prefix}{item.title}{suffix}: места: нет данных{oracle}")
                continue
            status = "✅" if item.can_enter else "❌"
            taken = item.budget_places - item.remaining_at_turn
            lines.append(
                f"  {item.priority}. {prefix}{item.title}{suffix}: "
                f"осталось {item.remaining_at_turn}/{item.budget_places} "
                f"(занято {taken}) {status}{oracle}"
            )
        if ties:
            lines.append(
                f"  ⚖️ На {ties} направлении(ях) ваш балл РОВНО равен проходному. "
                "Это тонкое место: при равенстве баллов проходят не все — "
                "спор решают баллы по профильным предметам и преимущественное "
                "право, и предсказать исход нельзя. Считайте это «может быть»."
            )
        if disagreements:
            lines.append(
                f"  ⚠️ Робот и сайт разошлись на {disagreements} направлении(ях) — "
                "смотрите колонку «сайт», она с официальной страницы вуза"
            )

    lines.append("")
    if result.dima_placed_program_key is None:
        if not result.dima_remaining_at_turn:
            lines.append(f"❌ Вы ({result.dima_score} б.) — не в очереди (нет согласия?)")
        else:
            lines.append(f"❌ Вы ({result.dima_score} б.) — не проходите ни по одному приоритету")
    else:
        via = "БВИ" if result.dima_placed_via == "bvi" else "общий конкурс"
        lines.append(f"→ Зачислится: {result.dima_placed_title or ''}")
        lines.append(f"  {result.dima_priority_used}-й приоритет · этап: {via}")
    lines.extend(_format_site_verdict(result))

    lines.extend(_format_optimistic(result))
    lines.extend(_format_verification(result.verification))

    if result.dima_competitors_by_program:
        lines.append("")
        lines.append("Список тех, кто впереди вас по каждому приоритету: /конкуренты <вуз> <код>")

    return "\n".join(lines)


def format_competitors(result: RobotSimulationResult, tracked_id: int) -> str:
    if result.error:
        return _format_error_response(f"🤖 Конкуренты — {result.university}", result.error)

    state = next((item for item in result.user_programs if item.tracked_id == tracked_id), None)
    if state is None:
        codes = [item.tracked_id for item in result.user_programs if item.tracked_id is not None]
        # Направления теперь берутся из пула, а не только из конфига — у части
        # (а иногда и у всех, как в примере МИРЭА/1616947) tracked_id может не
        # быть вовсе, потому что короткого числового кода из config/programs.json
        # для них просто нет. Пустой список кодов — это не «нет данных», это
        # штатный случай, и молчать хвостом после «Доступные коды: » нельзя.
        available = ", ".join(str(code) for code in codes) if codes else "нет — ни у одного вашего направления нет короткого кода из конфига"
        return f"🤖 Конкуренты — {result.university}\n\nНет направления с кодом {tracked_id}. Доступные коды: {available}"

    competitors = result.dima_competitors_by_program.get(state.program_key)
    okso = _okso_by_tracked_id().get(tracked_id)
    label = f"{okso} {state.title} (код {tracked_id})" if okso else f"{state.title} (код {tracked_id})"
    if competitors is None:
        return (
            f"🤖 Конкуренты — {result.university}\n\n"
            f"{label} не входит в ваши приоритеты — по нему нет данных о соперниках."
        )

    lines = [f"🤖 Конкуренты на {label} до вашего хода:"]
    if not competitors:
        lines.append("Никого — на момент вашего хода ещё никто сюда не зачислился.")
    else:
        for competitor in competitors:
            lines.extend(_format_competitor(competitor))
    return "\n".join(lines)
