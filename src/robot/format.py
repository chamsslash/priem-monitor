from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from ..config_loader import load_programs
from .direction_keys import okso_code_for_program
from .models import CompetitorBeforeDima, RobotSimulationResult, VerificationReport


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
        from .universities import SUPPORTED_UNIVERSITIES, is_pool_stale

        parser_name = SUPPORTED_UNIVERSITIES.get(result.university)
        if parser_name is not None and is_pool_stale(parser_name, result.fetched_at):
            return f"{title} (кэш от {fetched} · обновляю в фоне)"
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

    placement = report.placement
    if placement is not None:
        if placement.status == "match":
            where = placement.site_title or placement.robot_title or "—"
            lines.append(f"Прогноз: ✅ сходится с проходными сайта ({where})")
        elif placement.status == "mismatch":
            robot = placement.robot_title or "не проходит"
            site = placement.site_title or "не проходит"
            lines.append(f"⚠️ Прогноз расходится: робот → {robot}, сайт-порог → {site}")
        else:  # unavailable
            lines.append("ℹ️ Прогноз: нет проходных баллов на сайте — не сверить")
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
        for item in result.dima_remaining_at_turn:
            okso = okso_by_id.get(item.tracked_id) if item.tracked_id is not None else None
            prefix = f"{okso} " if okso else ""
            suffix = f" (код {item.tracked_id})" if item.tracked_id is not None else ""
            if item.budget_places is None or item.remaining_at_turn is None:
                lines.append(f"  {item.priority}. {prefix}{item.title}{suffix}: места: нет данных")
                continue
            status = "✅" if item.can_enter else "❌"
            taken = item.budget_places - item.remaining_at_turn
            lines.append(
                f"  {item.priority}. {prefix}{item.title}{suffix}: "
                f"осталось {item.remaining_at_turn}/{item.budget_places} "
                f"(занято {taken}) {status}"
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

    lines.extend(_format_verification(result.verification))

    if result.dima_competitors_by_program:
        lines.append("")
        lines.append("Список тех, кто впереди вас по каждому приоритету: /конкуренты <вуз> <код>")

    return "\n".join(lines)


def format_competitors(result: RobotSimulationResult, tracked_id: int) -> str:
    if result.error:
        return _format_error_response(f"🤖 Конкуренты — {result.university}", result.error)

    state = next((item for item in result.tracked_programs if item.tracked_id == tracked_id), None)
    if state is None:
        available = ", ".join(str(item.tracked_id) for item in result.tracked_programs)
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
