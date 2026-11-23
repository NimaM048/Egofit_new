"""Build strength and workout performance charts."""

from __future__ import annotations

from typing import Any

from django.utils.translation import gettext_lazy as _

from .dates import jalali_from_date


def get_performance_chart_data(user: Any) -> dict[str, Any]:
    """Return per-exercise workout record series for the analysis page chart."""
    from account.models import WorkoutPerformanceRecord

    records = (
        WorkoutPerformanceRecord.objects.filter(user=user)
        .select_related("exercise")
        .order_by("exercise__name", "recorded_on", "id")
    )
    exercises: dict[int | str, dict[str, Any]] = {}
    for record in records:
        exercise_label = record.exercise_label or str(_("حرکت حذف‌شده"))
        exercise_key = record.exercise_id or f"deleted:{exercise_label}"
        entry = exercises.setdefault(
            exercise_key,
            {"id": exercise_key, "name": exercise_label, "modes": {}},
        )
        mode_points = entry["modes"].setdefault(record.mode, {})
        date_label = jalali_from_date(record.recorded_on)
        point = mode_points.setdefault(
            date_label,
            {"value": 0.0, "repetitions": set(), "sets": []},
        )
        value = float(record.value)
        if value > point["value"]:
            point["value"] = value
        if record.repetitions:
            point["repetitions"].add(str(record.repetitions))
        point["sets"].append(record.set_number)

    mode_labels = {
        WorkoutPerformanceRecord.Mode.WEIGHT: _("وزنه (کیلوگرم)"),
        WorkoutPerformanceRecord.Mode.BODY_WEIGHT: _("وزن بدن (کیلوگرم)"),
        WorkoutPerformanceRecord.Mode.TIME: _("زمان (ثانیه)"),
    }
    items = []
    for entry in exercises.values():
        modes = {}
        for mode, points in entry["modes"].items():
            ordered = sorted(points.items(), key=lambda pair: pair[0])
            modes[mode] = {
                "label": str(mode_labels.get(mode, mode)),
                "labels": [label for label, _ in ordered],
                "values": [round(point["value"], 2) for _, point in ordered],
                "meta": [
                    {
                        "repetitions": "، ".join(sorted(point["repetitions"])),
                        "sets": len(point["sets"]),
                    }
                    for _, point in ordered
                ],
            }
        entry["modes"] = modes
        items.append(entry)
    items.sort(key=lambda item: item["name"])
    return {"performance_exercises": items, "performance_has_data": bool(items)}
