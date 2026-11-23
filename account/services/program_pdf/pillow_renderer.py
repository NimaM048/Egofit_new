"""Raster rendering and page assembly for the Pillow fallback."""

from __future__ import annotations

from io import BytesIO

from .background import _pdf_background_data
from .typography import _pdf_font_candidates
from .pillow_layout import _draw_fallback_day_table, _draw_fallback_summary_page
from .image_pdf import _build_image_pdf_with_links, _minimal_pdf


def _build_fallback_pdf(program, *, link_base_url="") -> bytes:
    """Create an image-based Persian PDF when optional PDF packages are absent.

    The fallback intentionally follows the same Sarbarg layout as the
    ReportLab renderer so development installations without ReportLab still
    produce the branded, Persian-capable worksheet.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return _minimal_pdf(
            [
                "EgoFit workout program",
                "Install reportlab, arabic-reshaper, and python-bidi for Persian PDF output.",
            ]
        )

    font_path = next((path for path in _pdf_font_candidates() if path.exists()), None)
    if font_path is None:
        return _minimal_pdf(
            [
                "EgoFit workout program",
                "No Persian font is available for PDF output.",
            ]
        )

    page_width, page_height = 864, 1232
    background_data = _pdf_background_data()
    days = list(program.days.all())
    schedule_page_count = (len(days) + 1) // 2
    pages = []
    page_links = []
    for page_index in range(schedule_page_count):
        page = _fallback_background_page(Image, background_data, page_width, page_height)
        draw = ImageDraw.Draw(page)
        top = 270
        current_page_links = []
        for slot_index in range(2):
            day_index = page_index * 2 + slot_index
            if day_index >= len(days):
                break
            table_height = _draw_fallback_day_table(
                draw,
                days[day_index],
                day_index + 1,
                font_path,
                x=51,
                top=top,
                width=762,
                link_base_url=link_base_url,
                link_specs=current_page_links,
            )
            top += table_height + 14
        pages.append(page)
        page_links.append(current_page_links)

    summary_page = _fallback_background_page(Image, background_data, page_width, page_height)
    summary_page_links = []
    _draw_fallback_summary_page(
        ImageDraw.Draw(summary_page),
        program,
        font_path,
        x=51,
        top=290,
        width=762,
        link_base_url=link_base_url,
        link_specs=summary_page_links,
    )
    pages.append(summary_page)
    page_links.append(summary_page_links)

    return _build_image_pdf_with_links(
        pages,
        page_links,
        page_width=page_width,
        page_height=page_height,
    )


def _fallback_background_page(image_module, background_data, page_width, page_height):
    if background_data:
        try:
            return image_module.open(BytesIO(background_data)).convert("RGB").resize(
                (page_width, page_height)
            )
        except Exception:
            pass
    return image_module.new("RGB", (page_width, page_height), "white")
