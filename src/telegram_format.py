from __future__ import annotations

import html
from datetime import datetime

STATUS_EMOJI = {
    "green": "🟢",
    "yellow": "🟡",
    "red": "🔴",
    "unknown": "⚪️",
}

STATUS_LABELS = {
    "green": "проходит",
    "yellow": "почти",
    "red": "не проходит",
    "unknown": "нет данных",
}


def _parse_generated_at(value: str | None) -> str:
    if not value:
        return "неизвестно"
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        local = dt.astimezone()
        return local.strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return value


def _row_key(row: dict) -> int:
    return int(row["program_id"])


def _status_from_rank(rank: int | None, places: int, buffer: int = 2) -> str:
    if rank is None:
        return "unknown"
    if places <= 0:
        return "unknown"
    if rank <= places:
        return "green"
    if rank <= places + buffer:
        return "yellow"
    return "red"


def compute_changes(
    old_rows: list[dict],
    new_rows: list[dict],
    *,
    rank_field: str = "rank_consent_priority1",
) -> list[str]:
    old_map = {_row_key(row): row for row in old_rows}
    changes: list[str] = []

    for row in new_rows:
        prev = old_map.get(_row_key(row))
        if prev is None:
            continue

        old_rank = prev.get(rank_field)
        new_rank = row.get(rank_field)
        if old_rank == new_rank:
            continue

        university = row.get("university", "")
        program = _display_program_name(row)

        places = row.get("budget_places", 0)
        old_rank_text = "—" if old_rank is None else str(old_rank)
        new_rank_text = "—" if new_rank is None else str(new_rank)
        emoji = STATUS_EMOJI.get(_status_from_rank(new_rank, places), STATUS_EMOJI["unknown"])
        changes.append(f"• {university} — {program}\n  место {old_rank_text}→{new_rank_text} {emoji}")

    return changes


def _count_by_status(rows: list[dict]) -> dict[str, int]:
    return _count_by_rank_field(rows, "rank_consent_priority1")


def _count_by_rank_field(rows: list[dict], rank_field: str) -> dict[str, int]:
    counts = {"green": 0, "yellow": 0, "red": 0, "unknown": 0}
    for row in rows:
        if row.get("error"):
            counts["unknown"] += 1
            continue
        places = int(row.get("budget_places") or 0)
        rank = row.get(rank_field)
        status = _status_from_rank(rank, places)
        counts[status] += 1
    return counts


def _format_counts_lines(counts: dict[str, int]) -> list[str]:
    return [
        f"🟢 Проходит: {counts['green']}",
        f"🟡 Почти: {counts['yellow']}",
        f"🔴 Не проходит: {counts['red']}",
        f"⚪️ Нет данных: {counts['unknown']}",
    ]


def format_summary(
    results: dict,
    *,
    title: str,
    changes_consent: list[str] | None = None,
    changes_priority1: list[str] | None = None,
    max_changes: int = 8,
) -> str:
    rows = results.get("rows", [])
    generated_at = _parse_generated_at(results.get("generated_at"))
    counts_consent = _count_by_rank_field(rows, "rank_consent")
    counts_priority1 = _count_by_rank_field(rows, "rank_consent_priority1")
    errors = [row for row in rows if row.get("error")]

    lines = [
        title,
        f"Обновлено: {generated_at}",
        "",
        "1. По согласиям",
        *_format_counts_lines(counts_consent),
        "",
        "2. По согласиям + 1-й приоритет",
        *_format_counts_lines(counts_priority1),
    ]

    if changes_consent:
        lines.append("")
        lines.append(f"Изменения по согласиям ({len(changes_consent)}):")
        lines.extend(changes_consent[:max_changes])
        if len(changes_consent) > max_changes:
            lines.append(f"… и ещё {len(changes_consent) - max_changes}")

    if changes_priority1:
        lines.append("")
        lines.append(f"Изменения по согласиям + пр.1 ({len(changes_priority1)}):")
        lines.extend(changes_priority1[:max_changes])
        if len(changes_priority1) > max_changes:
            lines.append(f"… и ещё {len(changes_priority1) - max_changes}")

    if errors:
        lines.append("")
        lines.append(f"⚠️ Ошибки парсеров: {len(errors)}")
        for row in errors[:5]:
            university = row.get("university", "")
            parser = row.get("parser", "")
            lines.append(f"• {university} ({parser})")

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:3990] + "\n…"
    return text


MEGA_DIRECTION_CODES = {
    "Программная инженерия": "09.03.04",
    "Прикладная математика и информатика": "01.03.02",
    "Информационные системы и технологии": "09.03.02",
    "Фундаментальная информатика и информационные технологии": "02.03.02",
    "Информатика и вычислительная техника": "09.03.01",
    "Прикладная математика": "01.03.04",
    "Прикладная информатика": "09.03.03",
}


def _mega_direction_name(mega_direction: str) -> str:
    text = mega_direction.strip()
    parts = text.split(" ", 1)
    if len(parts) == 2 and parts[0].isdigit():
        return parts[1].strip()
    return text


def format_mega_direction_label(mega_direction: str) -> str:
    name = _mega_direction_name(mega_direction)
    code = MEGA_DIRECTION_CODES.get(name)
    if code:
        return f"{code} {name}"
    return name


def _escape_html(text: str) -> str:
    return html.escape(text, quote=False)


def _display_program_name(row: dict) -> str:
    program = str(row.get("program", "")).replace("\n", " / ").strip()
    if program:
        return program
    return "Общий список"


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _format_rank_place(row: dict, rank_field: str, label: str) -> str:
    places = row.get("budget_places", "?")
    rank = row.get(rank_field)
    rank_text = "—" if rank is None else str(rank)
    emoji = STATUS_EMOJI.get(_status_from_rank(rank, int(row.get("budget_places") or 0)), STATUS_EMOJI["unknown"])
    return f"  {label}: {rank_text}/{places} {emoji}"


def _format_program_line(row: dict) -> str:
    program = _escape_html(_display_program_name(row))
    error = row.get("error")

    if error:
        return f"• {program}\n  ⚠️ {_escape_html(_truncate(str(error), 120))}"

    probability = _escape_html(str(row.get("probability_label", "")))
    lines = [
        f"• {program}",
        _format_rank_place(row, "rank_priority1", "1 приоритет"),
        _format_rank_place(row, "rank_consent", "согл."),
        _format_rank_place(row, "rank_consent_priority1", "согл.+пр.1"),
        f"  вероятность: {probability}",
    ]
    list_url = str(row.get("list_url", "")).strip()
    if list_url:
        lines.append(f'  <a href="{html.escape(list_url, quote=True)}">конкурсный список</a>')
    return "\n".join(lines)


def _group_by_mega_direction(rows: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        mega = str(row.get("mega_direction", "")).strip() or "Без мега-направления"
        label = format_mega_direction_label(mega)
        grouped.setdefault(label, []).append(row)

    for label in grouped:
        grouped[label].sort(
            key=lambda item: (
                {"green": 0, "yellow": 1, "red": 2, "unknown": 3}.get(item.get("status", "unknown"), 4),
                item.get("rank_consent_priority1") or 9999,
                _display_program_name(item),
            )
        )
    return dict(sorted(grouped.items()))


def _university_header(university: str, rows: list[dict], stamp: str, *, continued: bool = False) -> list[str]:
    title = f"🏛 {_escape_html(university)} ({len(rows)})"
    if continued:
        title += " — продолжение"

    counts_consent = _count_by_rank_field(rows, "rank_consent")
    counts_priority1 = _count_by_rank_field(rows, "rank_consent_priority1")

    lines = [title, stamp, ""]
    if not continued:
        lines.extend(
            [
                "1. По согласиям",
                *_format_counts_lines(counts_consent),
                "",
                "2. По согласиям + 1-й приоритет",
                *_format_counts_lines(counts_priority1),
                "",
            ]
        )
    return lines


def _mega_section_lines(mega_direction: str, mega_rows: list[dict]) -> list[str]:
    lines = [f"<b>{_escape_html(mega_direction)}</b>", ""]
    for row in mega_rows:
        lines.append(_format_program_line(row))
        lines.append("")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def format_university_messages(
    university: str,
    rows: list[dict],
    generated_at: str | None = None,
    *,
    max_len: int = 3800,
) -> list[str]:
    stamp = _parse_generated_at(generated_at)
    errors = sum(1 for row in rows if row.get("error"))
    grouped = _group_by_mega_direction(rows)

    messages: list[str] = []
    current_lines = _university_header(university, rows, stamp)

    if errors:
        current_lines.append(f"⚠️ Ошибки: {errors}")
        current_lines.append("")

    for mega_direction, mega_rows in grouped.items():
        section_lines = _mega_section_lines(mega_direction, mega_rows)
        section_text = "\n".join(section_lines)
        current_text = "\n".join(current_lines)
        separator = "\n\n" if current_lines else ""
        projected_len = len(current_text) + len(separator) + len(section_text)

        if current_lines and projected_len > max_len:
            messages.append(current_text)
            current_lines = _university_header(university, rows, stamp, continued=True)
            current_lines.append(section_text)
        elif current_lines:
            if current_lines[-1] != "":
                current_lines.append("")
            current_lines.extend(section_lines)
        else:
            current_lines.extend(section_lines)

    if current_lines:
        messages.append("\n".join(current_lines))

    return messages or [_university_header(university, rows, stamp)[0]]


def format_university_message(university: str, rows: list[dict], generated_at: str | None = None) -> str:
    return format_university_messages(university, rows, generated_at)[0]


def _group_by_university(rows: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        university = row.get("university", "Неизвестно")
        grouped.setdefault(university, []).append(row)
    for university in grouped:
        grouped[university].sort(
            key=lambda item: (
                item.get("mega_direction", ""),
                {"green": 0, "yellow": 1, "red": 2, "unknown": 3}.get(item.get("status", "unknown"), 4),
                item.get("rank_consent_priority1") or 9999,
                _display_program_name(item),
            )
        )
    return dict(sorted(grouped.items()))


def format_university_reports(results: dict) -> list[str]:
    rows = results.get("rows", [])
    generated_at = results.get("generated_at")
    grouped = _group_by_university(rows)
    messages: list[str] = []
    for university, uni_rows in grouped.items():
        messages.extend(format_university_messages(university, uni_rows, generated_at))
    return messages


def format_status_message(results: dict) -> str:
    text = format_summary(results, title="📊 Мониторинг поступления")
    return f"{text}\n\nВыберите вуз ниже для подробного отчёта:"


def format_push_message(old_results: dict, new_results: dict) -> str:
    old_rows = old_results.get("rows", [])
    new_rows = new_results.get("rows", [])
    changes_consent = compute_changes(old_rows, new_rows, rank_field="rank_consent")
    changes_priority1 = compute_changes(old_rows, new_rows, rank_field="rank_consent_priority1")
    has_changes = bool(changes_consent or changes_priority1)
    title = "🔔 Обновление списков" if has_changes else "🔔 Обновление списков (без изменений)"
    return format_summary(
        new_results,
        title=title,
        changes_consent=changes_consent or None,
        changes_priority1=changes_priority1 or None,
    )


def university_names(results: dict) -> list[str]:
    return list(_group_by_university(results.get("rows", [])).keys())


def university_rows(results: dict, university: str) -> list[dict]:
    grouped = _group_by_university(results.get("rows", []))
    return grouped.get(university, [])


def format_full_report(results: dict) -> str:
    rows = sorted(results.get("rows", []), key=lambda row: (row.get("university", ""), row.get("program", "")))
    generated_at = _parse_generated_at(results.get("generated_at"))
    lines = [f"Полный отчёт — {generated_at}", ""]

    for row in rows:
        emoji = STATUS_EMOJI.get(row.get("status", "unknown"), STATUS_EMOJI["unknown"])
        rank = row.get("rank_consent_priority1")
        rank_text = "—" if rank is None else str(rank)
        places = row.get("budget_places", "?")
        university = row.get("university", "")
        program = row.get("program", "").replace("\n", " / ")
        error = row.get("error")
        if error:
            lines.append(f"{emoji} {university} | {program}")
            lines.append(f"   ошибка: {error}")
        else:
            lines.append(f"{emoji} {university} | {program}")
            lines.append(f"   место {rank_text} / {places} бюджет | {row.get('probability_label', '')}")
        lines.append("")

    return "\n".join(lines).strip() + "\n"
