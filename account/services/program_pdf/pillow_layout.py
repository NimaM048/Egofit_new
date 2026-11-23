"""Layout the summary and workout days on Pillow pages."""

from __future__ import annotations

from .links import _pdf_movement_media_url
from .typography import _persian_number, _format_pdf_date, _fallback_rtl_visual, _wrap_fallback_line


def _fallback_movement_rows(day):
    rows = []
    if day is None:
        return rows
    for item in day.items.all():
        rows.append((item.movement.name, item.sets, item.reps, item.rest, item.note, item.movement))
        if item.superset_exercise_id:
            rows.append(
                (
                    item.superset_exercise.name,
                    item.superset_sets or item.sets,
                    item.superset_reps or item.reps,
                    item.superset_rest or item.rest,
                    item.note,
                    item.superset_exercise,
                )
            )
        if item.third_exercise_id:
            rows.append(
                (
                    item.third_exercise.name,
                    item.third_sets or item.sets,
                    item.third_reps or item.reps,
                    item.third_rest or item.rest,
                    item.note,
                    item.third_exercise,
                )
            )
    return rows


def _draw_fallback_day_table(
    draw,
    day,
    number,
    font_path,
    *,
    x,
    top,
    width,
    link_base_url="",
    link_specs=None,
):
    from PIL import ImageFont

    title_font = ImageFont.truetype(str(font_path), 18)
    header_font = ImageFont.truetype(str(font_path), 13)
    body_font = ImageFont.truetype(str(font_path), 13)
    widths = [280, 100, 60, 100, 222]
    title_height, header_height, row_height = 30, 42, 27
    movement_rows = _fallback_movement_rows(day)
    row_count = max(10, len(movement_rows))
    height = title_height + header_height + row_count * row_height
    right = x + width
    bottom = top + height
    ink = "#111111"

    title = f"برنامه {_persian_number(number)}"
    if day is not None and day.name:
        title = f"{title} — {day.name}"
    _fallback_cell_text(draw, title, (x, top, right, top + title_height), title_font, max_lines=1)
    draw.rectangle((x, top, right, top + title_height), outline=ink, width=1)
    draw.line((x, top + title_height, right, top + title_height), fill=ink, width=1)

    headers = ["توضیحات", "استراحت/دقیقه", "ست", "تکرار", "نام حرکت"]
    current_x = x
    for column_width, label in zip(widths, headers):
        next_x = current_x + column_width
        _fallback_cell_text(
            draw,
            label,
            (current_x, top + title_height, next_x, top + title_height + header_height),
            header_font,
            max_lines=2,
        )
        draw.rectangle(
            (current_x, top + title_height, next_x, top + title_height + header_height),
            outline=ink,
            width=1,
        )
        current_x = next_x

    values = [
        (row[4] or "—", row[3] or "—", row[1] or "—", row[2] or "—", row[0])
        for row in movement_rows
    ]
    values.extend([(" ", " ", " ", " ", " ")] * (row_count - len(values)))
    for row_index, row in enumerate(values):
        row_top = top + title_height + header_height + row_index * row_height
        current_x = x
        for column_width, value in zip(widths, row):
            next_x = current_x + column_width
            _fallback_cell_text(
                draw,
                value,
                (current_x, row_top, next_x, row_top + row_height),
                body_font,
                max_lines=2,
            )
            draw.rectangle(
                (current_x, row_top, next_x, row_top + row_height),
                outline=ink,
                width=1,
            )
            current_x = next_x
        if link_specs is not None and row_index < len(movement_rows):
            movement = movement_rows[row_index][5]
            href = _pdf_movement_media_url(movement, link_base_url)
            if href:
                movement_cell_x = x + sum(widths[:4])
                link_specs.append(
                    (
                        movement_cell_x,
                        row_top,
                        widths[4],
                        row_height,
                        href,
                    )
                )
    return height


def _draw_fallback_summary_page(
    draw,
    program,
    font_path,
    *,
    x,
    top,
    width,
    link_base_url="",
    link_specs=None,
):
    from PIL import ImageFont

    heading_font = ImageFont.truetype(str(font_path), 15)
    body_font = ImageFont.truetype(str(font_path), 13)
    note_font = ImageFont.truetype(str(font_path), 16)
    column_width = width // 2
    heading_height, corrective_height, details_height = 34, 130, 270
    ink = "#111111"
    right = x + width

    corrective_by_phase = {"warmup": [], "cooldown": []}
    for item in program.corrective_items.all():
        corrective_by_phase.setdefault(item.phase, []).append(item)

    def corrective_text(phase):
        items = corrective_by_phase.get(phase, [])
        if not items:
            return "—"
        return "\n".join(
            f"• {item.corrective_exercise.name}"
            + (f" — ست: {item.sets}" if item.sets else "")
            + (f" — تکرار/مدت: {item.reps}" if item.reps else "")
            + (f" — {item.note}" if item.note else "")
            for item in items
        )

    athlete = getattr(getattr(program, "user", None), "fullname", "") or "—"
    coach_user = getattr(program, "prescribed_by", None)
    coach = getattr(coach_user, "display_name", "") or getattr(coach_user, "fullname", "") or "—"
    info = "\n".join(
        [
            f"عنوان برنامه: {program.title}",
            f"نام ورزشکار: {athlete}",
            f"نام مربی: {coach}",
            f"تاریخ شروع: {_format_pdf_date(program.start_date)}",
            f"تاریخ اتمام: {_format_pdf_date(program.end_date) if program.end_date else '—'}",
        ]
    )
    note_body = "\n\n".join(
        item
        for item in (
            program.notes,
            f"مکمل و نکات غذایی:\n{program.supplements_note}" if program.supplements_note else "",
            f"توضیحات گرم کردن:\n{program.warmup_notes}" if program.warmup_notes else "",
            f"توضیحات سرد کردن:\n{program.cooldown_notes}" if program.cooldown_notes else "",
        )
        if item
    ) or "—"

    draw.rectangle(
        (x, top, right, top + heading_height + corrective_height + details_height),
        outline=ink,
        width=1,
    )
    draw.line((x + column_width, top, x + column_width, top + heading_height + corrective_height), fill=ink, width=1)
    draw.line((x, top + heading_height, right, top + heading_height), fill=ink, width=1)
    draw.line((x, top + heading_height + corrective_height, right, top + heading_height + corrective_height), fill=ink, width=1)
    draw.line((x + column_width, top + heading_height + corrective_height, x + column_width, top + heading_height + corrective_height + details_height), fill=ink, width=1)

    _fallback_cell_text(draw, "حرکات اصلاحی حین گرم کردن", (x, top, x + column_width, top + heading_height), heading_font, max_lines=2)
    _fallback_cell_text(draw, "حرکات اصلاحی حین سرد کردن", (x + column_width, top, right, top + heading_height), heading_font, max_lines=2)
    _fallback_cell_text(
        draw,
        corrective_text("warmup"),
        (x, top + heading_height, x + column_width, top + heading_height + corrective_height),
        body_font,
        max_lines=6,
    )
    _fallback_cell_text(
        draw,
        corrective_text("cooldown"),
        (x + column_width, top + heading_height, right, top + heading_height + corrective_height),
        body_font,
        max_lines=6,
    )
    if link_specs is not None:
        for phase, cell_x in (("warmup", x), ("cooldown", x + column_width)):
            items = corrective_by_phase.get(phase, [])
            if not items:
                continue
            cell_top = top + heading_height
            row_height = corrective_height / len(items)
            for index, item in enumerate(items):
                href = _pdf_movement_media_url(item.corrective_exercise, link_base_url)
                if href:
                    link_specs.append(
                        (
                            cell_x,
                            cell_top + index * row_height,
                            column_width,
                            row_height,
                            href,
                        )
                    )
    _fallback_cell_text(
        draw,
        "نکات، مکمل پیشنهادی و مصرف:",
        (x, top + heading_height + corrective_height, x + column_width, top + heading_height + corrective_height + 38),
        note_font,
        fill="#d00000",
        max_lines=2,
    )
    _fallback_cell_text(
        draw,
        note_body,
        (x, top + heading_height + corrective_height + 34, x + column_width, top + heading_height + corrective_height + details_height),
        body_font,
        max_lines=11,
    )
    _fallback_cell_text(
        draw,
        info,
        (x + column_width, top + heading_height + corrective_height, right, top + heading_height + corrective_height + details_height),
        body_font,
        max_lines=8,
    )


def _fallback_cell_text(
    draw,
    value,
    box,
    font,
    *,
    fill="#111111",
    max_lines=2,
):
    left, top, right, bottom = box
    max_width = max(1, right - left - 8)
    lines = []
    for raw_line in str(value or " ").splitlines() or [" "]:
        lines.extend(_wrap_fallback_line(raw_line, font, max_width, draw))
    if not lines:
        lines = [" "]
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        logical = lines[-1]
        while len(logical) > 1 and draw.textlength(
            _fallback_rtl_visual(logical + "…"), font=font
        ) > max_width:
            logical = logical[:-1]
        lines[-1] = logical + "…" if logical.strip() else "…"

    line_height = max(13, int(font.size * 1.25))
    total_height = line_height * len(lines)
    current_y = top + max(0, (bottom - top - total_height) // 2)
    for line in lines:
        visual = _fallback_rtl_visual(line)
        text_width = draw.textlength(visual, font=font)
        draw.text(
            (left + (right - left - text_width) / 2, current_y),
            visual,
            font=font,
            fill=fill,
        )
        current_y += line_height
