"""Labels, chart geometry, and display formatting for analysis."""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _


ANALYSIS_METRIC_OPTIONS = [
    {"key": "body_fat", "label": _("درصد چربی بدن")},
    {"key": "lean_mass", "label": _("توده بدون چربی")},
    {"key": "whr", "label": "WHR"},
    {"key": "whtr", "label": "WHtR"},
    {"key": "bmi", "label": "BMI"},
    {"key": "bmr", "label": "BMR"},
]


def analysis_metric_label(metric: str) -> str:
    for option in ANALYSIS_METRIC_OPTIONS:
        if option["key"] == metric:
            return option["label"]
    return _("درصد چربی بدن")


def segmented_bar(marker: int, *, zones: list[dict] | None = None) -> dict:
    """Build a bounded visual range marker for an analysis gauge."""
    return {
        "marker": max(4, min(marker, 96)),
        "zones": zones
        or [
            {"width": 22, "tone": "orange"},
            {"width": 24, "tone": "yellow"},
            {"width": 28, "tone": "green"},
            {"width": 26, "tone": "yellow"},
        ],
    }


def build_chart(points: list[int], *, width: int = 320, height: int = 160, top_pad: int = 18, bottom_pad: int = 34):
    """Render chart points as an SVG-compatible line and area path."""
    if not points:
        points = [0]

    usable_width = width - 40
    usable_height = height - top_pad - bottom_pad
    min_value = min(points)
    max_value = max(points)
    if min_value == max_value:
        max_value = min_value + 1

    rendered_points = []
    for index, value in enumerate(points):
        if len(points) == 1:
            x = 20 + usable_width / 2
        else:
            x = 20 + (usable_width * index / (len(points) - 1))
        normalized = (value - min_value) / (max_value - min_value)
        y = top_pad + ((1 - normalized) * usable_height)
        rendered_points.append({"x": round(x, 1), "y": round(y, 1), "value": value})

    path = "M " + " L ".join(f"{point['x']} {point['y']}" for point in rendered_points)
    baseline = height - bottom_pad
    area_path = f"{path} L {rendered_points[-1]['x']} {baseline} L {rendered_points[0]['x']} {baseline} Z"
    return rendered_points, path, area_path


def metric_round(metric: str, value: float):
    if metric in ("whr", "whtr"):
        return round(float(value), 2)
    if metric == "bmr":
        return int(round(float(value)))
    if metric in ("body_fat", "lean_mass", "bmi"):
        return round(float(value), 1)
    return round(float(value), 1)


def metric_unit(metric: str) -> str:
    if metric == "body_fat":
        return "%"
    if metric == "lean_mass":
        return str(_("کیلوگرم"))
    if metric == "bmr":
        return "kcal"
    return ""


def status_tone(text) -> str:
    """Map a Persian status label to a colour tone for the result tiles."""
    if not text:
        return "neutral"
    value = str(text)
    if any(keyword in value for keyword in ("نرمال", "مطلوب", "سالم", "ایده", "متعادل")):
        return "good"
    if any(keyword in value for keyword in ("چاق", "پرخطر", "خطر", "بالا")):
        return "danger"
    if any(keyword in value for keyword in ("اضافه", "متوسط", "کم‌وزن", "کم وزن", "لاغر")):
        return "warn"
    return "neutral"


def tile_number(value, *, decimals: int = 1):
    if value in (None, ""):
        return "—"
    if decimals == 0:
        return int(round(float(value)))
    return round(float(value), decimals)


def scale_series_for_chart(metric: str, values: list[float]) -> list[int]:
    scaled = []
    for value in values:
        if metric in ("whr", "whtr"):
            scaled.append(max(1, int(value * 100)))
        elif metric == "bmi":
            scaled.append(max(1, int(value * 10)))
        elif metric == "bmr":
            scaled.append(max(1, int(value / 10)))
        else:
            scaled.append(max(1, int(value)))
    return scaled


def format_metric_display(metric: str, value) -> str:
    if value in (None, ""):
        return _("ثبت نشده")
    if metric == "body_fat":
        return f"{value}%"
    if metric == "lean_mass":
        return f"{value} {_('کیلوگرم')}"
    if metric == "bmr":
        return str(int(value))
    return str(value)
