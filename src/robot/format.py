from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .models import P1EnrollmentBeforeDima, RobotSimulationResult


def _short_title(title: str, limit: int = 48) -> str:
    if len(title) <= limit:
        return title
    return title[: limit - 1] + "…"


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


def _format_p1_competitor(item: P1EnrollmentBeforeDima) -> list[str]:
    score_note = f", {item.score} б." if item.phase == "exam" and item.score > 0 else ""
    if item.phase == "bvi":
        score_note = ", БВИ"
    if item.via_p1_consent:
        return [f"• {item.code}{score_note} — пр.1 + согласие"]

    lines = [f"• {item.code}{score_note}, пр. на программу: {item.priority_on_program}"]
    if item.higher_priorities:
        higher_parts: list[str] = []
        for higher in item.higher_priorities:
            part = f"пр.{higher.priority} {_short_title(higher.title, 32)}"
            if higher.passing_score is not None:
                part += f" (проходной {higher.passing_score})"
            higher_parts.append(part)
        lines.append(f"  выше: {', '.join(higher_parts)}")
    return lines


def format_robot_result(result: RobotSimulationResult) -> str:
    if result.error:
        if "не найден в списках вуза" in result.error:
            return f"🤖 Робот — {result.university}\n\nВаш код не найден в списках этого вуза."
        return f"🤖 Робот — {result.university}\n\nОшибка: {result.error}"

    lines = [
        _data_header(result),
        "Только абитуриенты с согласием на зачисление",
        f"Направлений: {result.directions_total} (весь вуз) · в очереди: {result.total_people}",
        "Этап 1: БВИ (с согласием) → Этап 2: общий конкурс по баллам",
    ]
    if not result.require_consent:
        lines.insert(2, "⚠️ Режим без фильтра согласия")
    lines.append(f"БВИ с согласием: {result.bvi_people} · ЕГЭ с согласием: {result.exam_people}")

    if result.dima_remaining_at_turn:
        lines.append("")
        rank = result.dima_exam_queue_rank or "?"
        lines.append(
            f"Когда очередь доходит до вас ({result.dima_score} б., "
            f"{rank}-е место в очереди ЕГЭ, перед вами {result.dima_people_before} чел., "
            f"из них зачислено {result.dima_ahead_in_exam}):"
        )
        lines.append(f"Учитываются приоритеты ({len(result.dima_remaining_at_turn)}):")
        for item in result.dima_remaining_at_turn:
            if item.budget_places is None or item.remaining_at_turn is None:
                lines.append(f"  {item.priority}. {_short_title(item.title)}: места: нет данных")
                continue
            status = "✅" if item.can_enter else "❌"
            taken = item.budget_places - item.remaining_at_turn
            lines.append(
                f"  {item.priority}. {_short_title(item.title)}: "
                f"осталось {item.remaining_at_turn}/{item.budget_places} "
                f"(занято {taken}) {status}"
            )
        lines.append("Снимок — до вашего хода в очереди.")

    lines.append("")
    if result.dima_placed_program_key is None:
        if not result.dima_remaining_at_turn:
            lines.append(f"❌ Вы ({result.dima_score} б.) — не в очереди (нет согласия?)")
        else:
            lines.append(f"❌ Вы ({result.dima_score} б.) — не проходите ни по одному приоритету")
    else:
        via = "БВИ" if result.dima_placed_via == "bvi" else "общий конкурс"
        lines.append(f"→ Зачислится: {_short_title(result.dima_placed_title or '')}")
        lines.append(f"  {result.dima_priority_used}-й приоритет · этап: {via}")

    if result.dima_p1_competitors:
        lines.append("")
        p1_title = _short_title(result.dima_p1_title or "1-й приоритет")
        lines.append(f"Зачислены на ваш 1-й приоритет до вашего хода ({p1_title}):")
        for competitor in result.dima_p1_competitors:
            lines.extend(_format_p1_competitor(competitor))

    return "\n".join(lines)
