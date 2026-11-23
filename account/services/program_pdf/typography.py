"""Persian dates, fonts, and right-to-left text shaping for PDF output."""

from __future__ import annotations

from pathlib import Path
import re

from django.conf import settings


def _persian_number(number):
    digits = "۰۱۲۳۴۵۶۷۸۹"
    return "".join(digits[int(char)] for char in str(number))


def _format_pdf_date(value):
    if not value:
        return "—"
    try:
        import jdatetime

        return jdatetime.date.fromgregorian(date=value).strftime("%d/%m/%Y")
    except (ImportError, TypeError, ValueError):
        return value.strftime("%d/%m/%Y")


def _register_farsi_font() -> str:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for path in _pdf_font_candidates():
        if path.exists():
            try:
                pdfmetrics.registerFont(TTFont("EgoFitFarsi", str(path)))
                return "EgoFitFarsi"
            except Exception:
                continue
    raise ImportError(
        "No Persian-capable TrueType font is available for program PDF generation."
    )


def _get_rtl_text_transformer():
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
    except ImportError:
        raise ImportError(
            "arabic-reshaper and python-bidi are required for Persian program PDFs."
        )

    def transform(value):
        return get_display(arabic_reshaper.reshape(value))

    return transform


def _pdf_font_candidates() -> list[Path]:
    candidates = []
    try:
        configured_path = getattr(settings, "EGOFIT_PDF_FONT_PATH", "")
        base_dir = Path(settings.BASE_DIR)
    except Exception:
        configured_path = ""
        base_dir = Path(__file__).resolve().parents[3]
    if configured_path:
        candidates.append(Path(configured_path))

    try:
        import admin_persian

        package_fonts = (
            Path(admin_persian.__file__).resolve().parent
            / "static"
            / "admin_persian"
            / "fonts"
        )
        candidates.extend(
            [
                package_fonts / "sahel" / "Sahel.ttf",
                package_fonts / "vazir" / "Vazir.ttf",
                package_fonts / "samim" / "Samim.ttf",
                package_fonts / "yekan" / "Yekan.ttf",
                package_fonts / "tanha" / "Tanha.ttf",
            ]
        )
    except (ImportError, AttributeError, OSError):
        pass

    candidates.extend(
        [
            base_dir / "assets" / "fonts" / "Vazirmatn-Regular.ttf",
            base_dir / "assets" / "fonts" / "BNazanin.ttf",
            Path("C:/Windows/Fonts/tahoma.ttf"),
            Path("C:/Windows/Fonts/arial.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        ]
    )
    return candidates


_ARABIC_FORMS = {
    "ا": ("\ufe8d", "\ufe8e", None, None),
    "آ": ("\ufe81", "\ufe82", None, None),
    "ب": ("\ufe8f", "\ufe90", "\ufe91", "\ufe92"),
    "پ": ("\ufb56", "\ufb57", "\ufb58", "\ufb59"),
    "ت": ("\ufe95", "\ufe96", "\ufe97", "\ufe98"),
    "ث": ("\ufe99", "\ufe9a", "\ufe9b", "\ufe9c"),
    "ج": ("\ufe9d", "\ufe9e", "\ufe9f", "\ufea0"),
    "چ": ("\ufb7a", "\ufb7b", "\ufb7c", "\ufb7d"),
    "ح": ("\ufea1", "\ufea2", "\ufea3", "\ufea4"),
    "خ": ("\ufea5", "\ufea6", "\ufea7", "\ufea8"),
    "د": ("\ufea9", "\ufeaa", None, None),
    "ذ": ("\ufeab", "\ufeac", None, None),
    "ر": ("\ufead", "\ufeae", None, None),
    "ز": ("\ufeaf", "\ufeb0", None, None),
    "ژ": ("\xfb8a", "\ufb8b", None, None),
    "س": ("\ufeb1", "\ufeb2", "\ufeb3", "\ufeb4"),
    "ش": ("\ufeb5", "\ufeb6", "\ufeb7", "\ufeb8"),
    "ص": ("\ufeb9", "\ufeba", "\ufebb", "\ufebc"),
    "ض": ("\ufebd", "\ufebe", "\ufebf", "\ufec0"),
    "ط": ("\ufec1", "\ufec2", "\ufec3", "\ufec4"),
    "ظ": ("\ufec5", "\ufec6", "\ufec7", "\ufec8"),
    "ع": ("\ufec9", "\ufeca", "\ufecb", "\ufecc"),
    "غ": ("\ufecd", "\ufece", "\ufecf", "\ufed0"),
    "ف": ("\ufed1", "\ufed2", "\ufed3", "\ufed4"),
    "ق": ("\ufed5", "\ufed6", "\ufed7", "\ufed8"),
    "ک": ("\ufb8e", "\ufb8f", "\ufb90", "\ufb91"),
    "گ": ("\ufb92", "\ufb93", "\ufb94", "\ufb95"),
    "ل": ("\ufedd", "\ufede", "\ufedf", "\ufee0"),
    "م": ("\ufee1", "\ufee2", "\ufee3", "\ufee4"),
    "ن": ("\ufee5", "\ufee6", "\ufee7", "\ufee8"),
    "ه": ("\ufee9", "\ufeea", "\ufeeb", "\ufeec"),
    "و": ("\ufeed", "\ufeee", None, None),
    "ی": ("\ufeef", "\ufef0", "\ufef1", "\ufef2"),
    "\u06CC": ("\ufef1", "\ufef2", "\ufef3", "\ufef4"),
}


def _fallback_rtl_visual(value: str) -> str:
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display

        return get_display(arabic_reshaper.reshape(str(value)))
    except ImportError:
        pass

    tokens = re.findall(r"[\u0600-\u06ff\u200c]+|[A-Za-z]+|[0-9]+|\s+|.", str(value))
    visual_tokens = []
    for token in reversed(tokens):
        if re.fullmatch(r"[\u0600-\u06ff]+", token):
            visual_tokens.append(_reshape_arabic_run(token)[::-1])
        else:
            visual_tokens.append(token)
    return "".join(visual_tokens)


def _reshape_arabic_run(value: str) -> str:
    chars = [char for char in value if char != "\u200c"]
    shaped = []
    for index, char in enumerate(chars):
        forms = _ARABIC_FORMS.get(char)
        if not forms:
            shaped.append(char)
            continue
        previous = chars[index - 1] if index else ""
        following = chars[index + 1] if index + 1 < len(chars) else ""
        previous_forms = _ARABIC_FORMS.get(previous)
        following_forms = _ARABIC_FORMS.get(following)
        joins_previous = bool(previous_forms and previous_forms[2] and forms[1])
        joins_following = bool(following_forms and forms[2] and following_forms[1])
        if joins_previous and joins_following and forms[3]:
            shaped.append(forms[3])
        elif joins_previous:
            shaped.append(forms[1])
        elif joins_following and forms[2]:
            shaped.append(forms[2])
        else:
            shaped.append(forms[0])
    return "".join(shaped)


def _wrap_fallback_line(value, font, max_width, draw):
    words = str(value).split()
    if not words:
        return [""]
    lines = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        visual = _fallback_rtl_visual(candidate)
        if font.getlength(visual) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines
