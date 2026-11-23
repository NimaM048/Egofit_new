"""Vector rendering for workout program PDFs."""

from __future__ import annotations

import html
from io import BytesIO

from .links import _pdf_movement_media_url
from .background import _pdf_background_data
from .typography import (
    _persian_number,
    _format_pdf_date,
    _register_farsi_font,
    _get_rtl_text_transformer,
)


def _build_reportlab_pdf(program, *, link_base_url="") -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas
    from reportlab.platypus import Paragraph, Table, TableStyle

    font_name = _register_farsi_font()
    reshape_text = _get_rtl_text_transformer()

    output = BytesIO()
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "SarbargProgramTitle",
        parent=styles["BodyText"],
        fontName=font_name,
        fontSize=10.5,
        leading=12,
        alignment=TA_CENTER,
        textColor=colors.black,
    )
    header_style = ParagraphStyle(
        "SarbargTableHeader",
        parent=styles["BodyText"],
        fontName=font_name,
        fontSize=8.2,
        leading=9.5,
        alignment=TA_CENTER,
        textColor=colors.black,
    )
    body_style = ParagraphStyle(
        "SarbargTableBody",
        parent=styles["BodyText"],
        fontName=font_name,
        fontSize=7.4,
        leading=8.6,
        alignment=TA_CENTER,
        textColor=colors.black,
    )
    note_heading_style = ParagraphStyle(
        "SarbargNoteHeading",
        parent=styles["BodyText"],
        fontName=font_name,
        fontSize=9.5,
        leading=12,
        alignment=TA_CENTER,
        textColor=colors.red,
    )
    info_style = ParagraphStyle(
        "SarbargInfo",
        parent=styles["BodyText"],
        fontName=font_name,
        fontSize=8.5,
        leading=13,
        alignment=TA_CENTER,
        textColor=colors.black,
    )

    def paragraph(value, style=body_style, *, href=""):
        transformed = reshape_text(str(value or ""))
        content = html.escape(transformed).replace("\n", "<br/>")
        if href:
            escaped_href = html.escape(href, quote=True)
            content = (
                f'<link href="{escaped_href}">'
                f'<u><font color="#0563C1">{content}</font></u>'
                "</link>"
            )
        return Paragraph(content, style)

    def movement_paragraph(movement, style=body_style):
        return paragraph(
            movement.name,
            style,
            href=_pdf_movement_media_url(movement, link_base_url),
        )

    page_width, page_height = letter
    content_x = 0.5 * inch
    content_width = 7.48 * inch
    table_widths = [2.754 * inch, 0.986 * inch, 0.589 * inch, 0.986 * inch, 2.165 * inch]
    background = _pdf_background_reader(ImageReader)
    document = canvas.Canvas(output, pagesize=letter)
    document.setTitle(str(program.title))
    document.setAuthor("EgoFit")

    days = list(program.days.all())
    schedule_page_count = (len(days) + 1) // 2

    for page_index in range(schedule_page_count):
        _draw_pdf_background(document, background, page_width, page_height)
        top = page_height - 2.18 * inch
        for slot_index in range(2):
            day_index = page_index * 2 + slot_index
            if day_index >= len(days):
                break
            table = _build_sarbarg_day_table(
                days[day_index],
                day_index + 1,
                paragraph,
                title_style,
                header_style,
                table_widths,
                colors,
                movement_paragraph=movement_paragraph,
            )
            _, table_height = table.wrapOn(document, content_width, page_height)
            table.drawOn(document, content_x, top - table_height)
            top -= table_height + 0.16 * inch
        document.showPage()

    _draw_pdf_background(document, background, page_width, page_height)
    _draw_sarbarg_summary_page(
        document=document,
        program=program,
        paragraph=paragraph,
        info_style=info_style,
        note_heading_style=note_heading_style,
        content_x=content_x,
        content_width=content_width,
        page_height=page_height,
        colors=colors,
        link_base_url=link_base_url,
    )
    document.showPage()
    document.save()
    return output.getvalue()


def _build_sarbarg_day_table(
    day,
    number,
    paragraph,
    title_style,
    header_style,
    table_widths,
    colors,
    movement_paragraph=None,
):
    from reportlab.platypus import Table, TableStyle

    movement_paragraph = movement_paragraph or (lambda movement: paragraph(movement.name))
    title = f"برنامه {_persian_number(number)}"
    if day is not None and day.name:
        title = f"{title} — {day.name}"

    rows = [[paragraph(title, title_style), "", "", "", ""], [
        paragraph("توضیحات", header_style),
        paragraph("استراحت (ثانیه)", header_style),
        paragraph("ست", header_style),
        paragraph("تکرار", header_style),
        paragraph("نام حرکت", header_style),
    ]]

    movement_rows = []
    if day is not None:
        for item in day.items.all():
            movement_rows.append((item.movement, item.sets, item.reps, item.rest, item.note))
            if item.superset_exercise_id:
                movement_rows.append(
                    (
                        item.superset_exercise,
                        item.superset_sets or item.sets,
                        item.superset_reps or item.reps,
                        item.superset_rest or item.rest,
                        item.note,
                    )
                )
            if item.third_exercise_id:
                movement_rows.append(
                    (
                        item.third_exercise,
                        item.third_sets or item.sets,
                        item.third_reps or item.reps,
                        item.third_rest or item.rest,
                        item.note,
                    )
                )

    for movement, sets, reps, rest, note in movement_rows:
        rows.append(
            [
                paragraph(note or "\u00a0"),
                paragraph(rest or "—"),
                paragraph(sets or "—"),
                paragraph(reps or "—"),
                movement_paragraph(movement),
            ]
        )

    # Keep the characteristic writing space from the reference document.
    for _ in range(max(0, 10 - len(movement_rows))):
        rows.append([paragraph("\u00a0")] * 5)

    table = Table(
        rows,
        colWidths=table_widths,
    )
    table.setStyle(
        TableStyle(
            [
                ("SPAN", (0, 0), (-1, 0)),
                ("GRID", (0, 0), (-1, -1), 0.55, colors.black),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 2.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
            ]
        )
    )
    return table


def _draw_sarbarg_summary_page(
    *,
    document,
    program,
    paragraph,
    info_style,
    note_heading_style,
    content_x,
    content_width,
    page_height,
    colors,
    link_base_url="",
):
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, Table, TableStyle

    corrective_by_phase = {"warmup": [], "cooldown": []}
    for item in program.corrective_items.all():
        corrective_by_phase.setdefault(item.phase, []).append(item)

    def corrective_paragraph(phase):
        items = corrective_by_phase.get(phase, [])
        if not items:
            return paragraph("—", info_style)

        lines = []
        reshape_text = _get_rtl_text_transformer()
        for item in items:
            line_text = f"• {item.corrective_exercise.name}"
            if item.sets:
                line_text += f" — ست: {item.sets}"
            if item.reps:
                line_text += f" — تکرار/مدت: {item.reps}"
            if item.note:
                line_text += f" — {item.note}"

            content = html.escape(reshape_text(line_text))
            href = _pdf_movement_media_url(item.corrective_exercise, link_base_url)
            if href:
                content = (
                    f'<link href="{html.escape(href, quote=True)}">'
                    f'<u><font color="#0563C1">{content}</font></u>'
                    "</link>"
                )
            lines.append(content)
        return Paragraph("<br/>".join(lines), info_style)

    athlete = getattr(getattr(program, "user", None), "fullname", "") or "—"
    coach = getattr(getattr(program, "prescribed_by", None), "display_name", "") or getattr(
        getattr(program, "prescribed_by", None), "fullname", ""
    ) or "—"
    info_lines = [
        f"عنوان برنامه: {program.title}",
        f"نام ورزشکار: {athlete}",
        f"نام مربی: {coach}",
        f"تاریخ شروع: {_format_pdf_date(program.start_date)}",
        f"تاریخ اتمام: {_format_pdf_date(program.end_date) if program.end_date else '—'}",
    ]
    note_lines = [
        program.notes,
        f"مکمل و نکات تغذیه‌ای:\n{program.supplements_note}" if program.supplements_note else "",
        f"توضیحات گرم کردن:\n{program.warmup_notes}" if program.warmup_notes else "",
        f"توضیحات سرد کردن:\n{program.cooldown_notes}" if program.cooldown_notes else "",
    ]
    note_body = "\n\n".join(item for item in note_lines if item).strip() or "—"

    summary = Table(
        [
            [
                paragraph("حرکات اصلاحی حین گرم کردن", info_style),
                paragraph("حرکات اصلاحی حین سرد کردن", info_style),
            ],
            [
                corrective_paragraph("warmup"),
                corrective_paragraph("cooldown"),
            ],
            [
                [
                    Paragraph(
                        html.escape(_get_rtl_text_transformer()("نکات، مکمل پیشنهادی و مصرف:")),
                        note_heading_style,
                    ),
                    paragraph(note_body, info_style),
                ],
                paragraph("\n".join(info_lines), info_style),
            ],
        ],
        colWidths=[content_width / 2, content_width / 2],
    )
    summary.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.55, colors.black),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    _, summary_height = summary.wrapOn(document, content_width, page_height)
    summary.drawOn(document, content_x, page_height - 2.58 * inch - summary_height)


def _draw_pdf_background(document, background, page_width, page_height):
    if background is not None:
        document.drawImage(
            background,
            0,
            0,
            width=page_width,
            height=page_height,
            preserveAspectRatio=False,
            mask="auto",
        )


def _pdf_background_reader(image_reader_class):
    background_data = _pdf_background_data()
    if background_data:
        try:
            return image_reader_class(BytesIO(background_data))
        except Exception:
            pass
    return None
