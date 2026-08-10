from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .models import (
    CompetitorBeforeDima,
    DimaPrioritySnapshot,
    RobotSimulationResult,
    VerificationReport,
)


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
        lines.append(
            "ℹ️ Конкурсные списки для сверки: оракул сайта недоступен — "
            "вуз не отмечает проходящих, сверить прогноз не с чем"
        )
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

    # Порог посчитан по опубликованному списку зачисленных. Вопрос тут НЕ «есть
    # ли вы в списке» — согласие не подавалось, и вас там нет по определению, а
    # «прошли бы, если бы подали»: сравниваем балл с минимальным среди тех, кого
    # вуз уже зачислил.
    if placement.source == "published":
        if placement.site_key is None:
            lines = [
                "  По спискам зачисленных: балла не хватает ни на одном "
                "из ваших приоритетов ❌"
            ]
        elif placement.status == "boundary":
            lines = [
                f"  По спискам зачисленных: «{placement.site_title}» ⚖️ ИДЁТЕ ПО ГРАНИ",
                f"  Ваш балл ровно равен минимальному среди зачисленных "
                f"({placement.site_cutoff}). При равенстве проходят не все: спор "
                f"решают профильные предметы и преимущественное право",
            ]
        else:
            lines = [
                f"  По спискам зачисленных: прошли бы на «{placement.site_title}» ✅",
                f"  Минимальный балл среди зачисленных туда — {placement.site_cutoff}, "
                f"у вас {result.dima_score}",
            ]
        if placement.status == "match":
            lines.append("  Прогноз робота совпал ✅")
        elif placement.status == "mismatch":
            # Не «верить сайту»: порог по зачисленным и каскад робота отвечают на
            # разные вопросы. Порог — итог уже состоявшегося зачисления, каскад
            # считает по оставшимся местам, и разойтись они могут без ошибки в
            # обоих. Показываем оба ответа, не назначая победителя.
            lines.append(
                "  ⚠️ Робот предсказывает другое: он считает по оставшимся местам, "
                "а порог выше — по уже состоявшемуся зачислению"
            )
        return lines

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


def _format_oracle(item: DimaPrioritySnapshot, score: int, *, published: bool = False) -> str:
    """Колонка «сайт»: что говорит официальный проходной балл по этому направлению.

    Показывается ВСЕГДА, а не только при расхождении. Робот — модель, и он может
    ошибаться; вердикт вуза о том, кто проходит среди согласных, взят прямо с его
    страницы и в расчёт робота не входит. Видеть их рядом важнее, чем видеть один
    красивый ответ: если они разошлись, решает сайт, а не мы.
    """
    if item.site_cutoff is None:
        return ""
    threshold = "любой балл" if item.site_cutoff == 0 else f"проходной {item.site_cutoff}"
    # Порог посчитан по списку зачисленных (см. verification.published_cutoffs).
    # Вердикт «прошёл бы / не хватило» печатаем, а вот флаг расхождения с роботом
    # — нет: каскад считает по оставшимся местам, а этот порог — итог уже
    # состоявшегося зачисления, и разойтись они могут без ошибки в обоих.
    # Спор двух оракулов в одном сообщении разбирается ниже, в вердикте.
    if published:
        mark = "⚖️ РОВНО НА ГРАНИ" if item.is_tie(score) else ("✅" if item.site_lets_in(score) else "❌")
        return f" · по зачисленным: минимум {item.site_cutoff} {mark}"
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
        placement = result.verification.placement if result.verification else None
        published = placement is not None and placement.source == "published"
        disagreements = 0
        ties = 0
        # Номер здесь — не «сырой» приоритет с сайта (в нём после схлопывания
        # дублей могут быть дыры), а порядковый номер В ЭТОМ СПИСКЕ. Он же —
        # тот самый номер, который принимает /конкуренты (format_competitors
        # ищет направление по позиции в result.user_programs, построенном
        # той же сортировкой по приоритету, что и этот список).
        for number, item in enumerate(result.dima_remaining_at_turn, start=1):
            if item.is_tie(result.dima_score):
                ties += 1
            prefix = f"{item.okso_code} " if item.okso_code else ""
            hint = f" (обратиться: /конкуренты {result.university} {number})"
            oracle = _format_oracle(item, result.dima_score, published=published)
            if not published and item.agrees_with_site(result.dima_score) is False:
                disagreements += 1
            if item.budget_places is None or item.remaining_at_turn is None:
                lines.append(f"  {number}. {prefix}{item.title}: места: нет данных{oracle}{hint}")
                continue
            status = "✅" if item.can_enter else "❌"
            taken = item.budget_places - item.remaining_at_turn
            lines.append(
                f"  {number}. {prefix}{item.title}: "
                f"осталось {item.remaining_at_turn}/{item.budget_places} "
                f"(занято {taken}) {status}{oracle}{hint}"
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
        lines.append("Список тех, кто впереди вас по каждому приоритету: /конкуренты <вуз> <номер приоритета>")

    return "\n".join(lines)


def format_competitors(result: RobotSimulationResult, priority_number: int) -> str:
    """priority_number — не tracked_id из конфига (направления теперь произвольные,
    у части из них — а иногда и у всех, как в примере МИРЭА/1616947 — короткого id
    просто нет), а позиция в result.user_programs: том же списке и в том же
    порядке, что печатает format_robot_result в блоке «Учитываются приоритеты»."""
    if result.error:
        return _format_error_response(f"🤖 Конкуренты — {result.university}", result.error)

    total = len(result.user_programs)
    if priority_number < 1 or priority_number > total:
        available = f"от 1 до {total}" if total else "нет — в вашем списке нет ни одного направления"
        return (
            f"🤖 Конкуренты — {result.university}\n\n"
            f"Нет приоритета номер {priority_number}. Доступные номера: {available}"
        )

    state = result.user_programs[priority_number - 1]
    competitors = result.dima_competitors_by_program.get(state.program_key)
    okso = f"{state.okso_code} " if state.okso_code else ""
    label = f"{okso}{state.title} (приоритет {priority_number})"
    if competitors is None:
        return (
            f"🤖 Конкуренты — {result.university}\n\n"
            f"{label}: данных о соперниках нет."
        )

    lines = [f"🤖 Конкуренты на {label} до вашего хода:"]
    if not competitors:
        lines.append("Никого — на момент вашего хода ещё никто сюда не зачислился.")
    else:
        for competitor in competitors:
            lines.extend(_format_competitor(competitor))
    return "\n".join(lines)
