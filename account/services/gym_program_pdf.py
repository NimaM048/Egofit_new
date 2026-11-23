"""Stable interface for producing a workout program PDF."""

from .program_pdf.links import _pdf_link_base_url
from .program_pdf.reportlab_renderer import _build_reportlab_pdf
from .program_pdf.pillow_renderer import _build_fallback_pdf


def build_program_pdf(program, request=None) -> bytes:
    """Render a program as a Persian-capable PDF when ReportLab is available."""
    link_base_url = _pdf_link_base_url(request)
    try:
        return _build_reportlab_pdf(program, link_base_url=link_base_url)
    except ImportError:
        return _build_fallback_pdf(program, link_base_url=link_base_url)
