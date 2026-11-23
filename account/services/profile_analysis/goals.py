"""Build an athlete goal and progress history from available measurements."""

from __future__ import annotations

import math
from typing import Any

from django.utils.translation import gettext_lazy as _

from account.domain.body_composition.calculator import (
    CaliperSkinfolds,
    CircumferenceMeasures,
    calculate_bmi,
    calculate_lean_mass,
    calculate_whtr,
    resolve_body_fat_result,
)

from .dates import age_at_measurement, measurement_datetime, record_jalali_date
from .measurements import (
    find_caliper_for_datetime,
    find_circumference_for_datetime,
    whr_from_circumference,
    resolve_body_fat_formula,
)


def build_smart_goal_data(
    user: Any,
    *,
    circ_records: list[Any],
    cal_records: list[Any],
    age: int | None,
    body_fat_formula: str | None = None,
) -> dict[str, Any]:
    """Build an evidence-informed starting goal from the available tests.

    The goal is intentionally a planning aid rather than a clinical
    prescription.  It keeps fat loss focused on preserving fat-free mass,
    uses a conservative lean-mass gain allowance, and never invents a
    missing measurement.  Historical test rows are returned separately so
    the UI can show the path to the target instead of only a single number.
    """
    selected_formula = resolve_body_fat_formula(body_fat_formula)
    if age is not None and age < 18:
        return {"available": False}
    gender = user.gender
    profile_weight = float(user.weight_kg) if user.weight_kg not in (None, "") else None
    profile_height = float(user.height_cm) if user.height_cm not in (None, "") else None

    def body_fat_for(circumference, caliper, height_cm, weight_kg, measurement_record):
        skinfolds = CaliperSkinfolds.from_model(caliper)
        measures = CircumferenceMeasures.from_model(circumference)
        measurement_age = age_at_measurement(user, measurement_record)
        result = resolve_body_fat_result(
            skinfolds=skinfolds,
            circumference=measures,
            height_cm=height_cm,
            age=measurement_age,
            gender=gender,
            formula=selected_formula,
            weight_kg=weight_kg,
        )
        # Auto mode prefers a caliper result.  If no caliper data exists,
        # use the validated Navy circumference estimate when possible.
        if result is None and selected_formula == "auto":
            result = resolve_body_fat_result(
                skinfolds=skinfolds,
                circumference=measures,
                height_cm=height_cm,
                age=measurement_age,
                gender=gender,
                formula="circumference",
                weight_kg=weight_kg,
            )
        return (
            result.body_fat_percent if result else None,
            result.formula_name if result else None,
        )

    observations: list[dict[str, Any]] = []
    paired_caliper_ids: set[int] = set()

    for record in circ_records:
        paired = find_caliper_for_datetime(user, measurement_datetime(record), caliper_records=cal_records)
        if paired is not None:
            paired_caliper_ids.add(id(paired))
        weight = float(record.weight_kg) if record.weight_kg not in (None, "") else profile_weight
        height = float(record.height_cm) if record.height_cm not in (None, "") else profile_height
        body_fat, formula_name = body_fat_for(record, paired, height, weight, record)
        whr = whr_from_circumference(record)
        observations.append(
            {
                "_sort_key": measurement_datetime(record),
                "date": record_jalali_date(record),
                "source": str(
                    _("آزمون محیط بدن و کالیپر") if paired is not None else _("آزمون محیط بدن")
                ),
                "weight": weight,
                "body_fat": body_fat,
                "lean_mass": calculate_lean_mass(weight, body_fat),
                "whr": whr,
                "formula_name": formula_name,
            }
        )

    for record in cal_records:
        if id(record) in paired_caliper_ids:
            continue
        paired_circumference = find_circumference_for_datetime(
            user,
            measurement_datetime(record),
            circumference_records=circ_records,
        )
        weight = (
            float(paired_circumference.weight_kg)
            if paired_circumference and paired_circumference.weight_kg not in (None, "")
            else profile_weight
        )
        height = (
            float(paired_circumference.height_cm)
            if paired_circumference and paired_circumference.height_cm not in (None, "")
            else profile_height
        )
        body_fat, formula_name = body_fat_for(None, record, height, weight, record)
        observations.append(
            {
                "_sort_key": measurement_datetime(record),
                "date": record_jalali_date(record),
                "source": str(_("آزمون کالیپر")),
                "weight": weight,
                "body_fat": body_fat,
                "lean_mass": calculate_lean_mass(weight, body_fat),
                "whr": None,
                "formula_name": formula_name,
            }
        )

    observations.sort(key=lambda item: item["_sort_key"])
    if not observations and profile_weight is not None:
        observations.append(
            {
                "_sort_key": None,
                "date": str(_("پروفایل فعلی")),
                "source": str(_("اطلاعات پروفایل")),
                "weight": profile_weight,
                "body_fat": None,
                "lean_mass": None,
                "whr": None,
                "formula_name": None,
            }
        )

    def latest_value(key: str):
        for observation in reversed(observations):
            if observation.get(key) is not None:
                return observation[key]
        return None

    current_weight = latest_value("weight") or profile_weight
    current_body_fat = latest_value("body_fat")
    current_whr = latest_value("whr")
    current_lean_mass = calculate_lean_mass(current_weight, current_body_fat)
    current_bmi = calculate_bmi(current_weight, profile_height)

    latest_circumference = circ_records[-1] if circ_records else None
    current_waist = (
        float(latest_circumference.waist_cm)
        if latest_circumference and latest_circumference.waist_cm not in (None, "")
        else None
    )
    current_whtr = calculate_whtr(current_waist, profile_height)

    if all(value is None for value in (current_weight, current_body_fat, current_whr)):
        return {"available": False}

    if gender == "male":
        body_fat_lower, body_fat_ideal = 12.0, 18.0
        whr_target_limit = 0.90
    elif gender == "female":
        body_fat_lower, body_fat_ideal = 20.0, 25.0
        whr_target_limit = 0.80
    else:
        body_fat_lower = body_fat_ideal = whr_target_limit = None

    # The decision order protects against prescribing weight gain to a
    # person whose BMI is low but whose measured fat percentage is high.
    if current_body_fat is not None and body_fat_ideal is not None and current_body_fat > body_fat_ideal + 2:
        goal_type = "loss"
    elif current_body_fat is not None and body_fat_lower is not None and current_body_fat < body_fat_lower:
        goal_type = "gain"
    elif current_bmi is not None and current_bmi < 18.5:
        goal_type = "gain"
    elif (
        current_body_fat is None
        and (
            (current_bmi is not None and current_bmi >= 25)
            or (current_whtr is not None and current_whtr >= 0.50)
            or (current_whr is not None and whr_target_limit is not None and current_whr > whr_target_limit)
        )
    ):
        goal_type = "loss"
    elif current_whr is not None and whr_target_limit is not None and current_whr > whr_target_limit + 0.02:
        goal_type = "loss"
    elif current_bmi is not None and 18.5 <= current_bmi < 25:
        goal_type = "recomposition"
    else:
        goal_type = "maintain"

    goal_titles = {
        "loss": str(_("کاهش چربی و حفظ تودهٔ بدون چربی")),
        "gain": str(_("افزایش وزن با اولویت تودهٔ بدون چربی")),
        "recomposition": str(_("بازترکیب بدن؛ کاهش تدریجی چربی و افزایش عضله")),
        "maintain": str(_("حفظ وضعیت فعلی و پایش منظم شاخص‌ها")),
    }
    goal_descriptions = {
        "loss": str(_("تمرکز اصلی روی کاهش چربی است و تودهٔ بدون چربی باید تا حد ممکن حفظ شود.")),
        "gain": str(_("افزایش وزن به‌صورت تدریجی و با اولویت ساخت تودهٔ بدون چربی هدف‌گذاری شده است.")),
        "recomposition": str(_("وزن ممکن است تغییر زیادی نکند؛ کیفیت تغییرات چربی و تودهٔ بدون چربی مهم‌تر است.")),
        "maintain": str(_("شاخص‌های فعلی در محدودهٔ قابل‌قبول برنامه‌ریزی هستند؛ اندازه‌گیری منظم برای حفظ روند لازم است.")),
    }

    target_body_fat = None
    if current_body_fat is not None and body_fat_ideal is not None:
        if goal_type == "loss":
            target_body_fat = body_fat_ideal
        elif goal_type == "gain":
            target_body_fat = min(body_fat_ideal, max(body_fat_lower, current_body_fat + 2))
        elif goal_type == "recomposition":
            target_body_fat = max(body_fat_lower, min(body_fat_ideal, current_body_fat - 2))
        else:
            target_body_fat = current_body_fat

    target_lean_mass = None
    if current_lean_mass is not None:
        lean_multiplier = {
            "loss": 1.00,
            "gain": 1.05,
            "recomposition": 1.03,
            "maintain": 1.00,
        }[goal_type]
        target_lean_mass = round(current_lean_mass * lean_multiplier, 1)

    target_weight = None
    if target_lean_mass is not None and target_body_fat is not None and target_body_fat < 100:
        target_weight = round(target_lean_mass / (1 - target_body_fat / 100), 1)
    elif current_weight is not None and profile_height is not None:
        height_m = profile_height / 100
        if goal_type == "loss":
            target_weight = round(min(current_weight, 24.9 * height_m * height_m), 1)
        elif goal_type == "gain":
            target_weight = round(max(current_weight, 20.0 * height_m * height_m), 1)
        else:
            target_weight = round(current_weight, 1)

    target_whr = None
    if current_whr is not None and whr_target_limit is not None:
        target_whr = round(min(current_whr, whr_target_limit), 2)
    elif current_whr is not None:
        target_whr = round(current_whr, 2)

    if current_weight is not None and target_weight is not None and goal_type in ("loss", "gain"):
        weekly_rate = current_weight * (0.005 if goal_type == "loss" else 0.0025)
        timeframe_weeks = max(8, min(52, math.ceil(abs(target_weight - current_weight) / max(weekly_rate, 0.1))))
    else:
        timeframe_weeks = 16

    def goal_display(value, unit: str, decimals: int = 1) -> str:
        if value is None:
            return str(_("نیازمند تست"))
        if decimals == 0:
            rendered = str(int(round(float(value))))
        else:
            rendered = f"{float(value):.{decimals}f}"
        return f"{rendered} {unit}".strip()

    def progress_for(key: str, target, tolerance: float) -> int | None:
        current = latest_value(key)
        if current is None or target is None:
            return None
        if abs(float(current) - float(target)) <= tolerance:
            return 100
        values = [float(row[key]) for row in observations if row.get(key) is not None]
        if len(values) < 2 or values[0] == float(target):
            return 0
        raw = ((values[-1] - values[0]) / (float(target) - values[0])) * 100
        return int(max(0, min(100, round(raw))))

    metric_specs = [
        ("weight", str(_("وزن")), current_weight, target_weight, "کیلوگرم", 1, 0.1),
        ("body_fat", str(_("درصد چربی بدن")), current_body_fat, target_body_fat, "%", 1, 0.2),
        ("lean_mass", str(_("تودهٔ بدون چربی")), current_lean_mass, target_lean_mass, "کیلوگرم", 1, 0.1),
        ("whr", "WHR", current_whr, target_whr, "", 2, 0.01),
    ]
    goal_metrics = []
    for key, label, current, target, unit, decimals, tolerance in metric_specs:
        progress = progress_for(key, target, tolerance)
        if current is None:
            status = str(_("نیازمند ثبت تست"))
        elif target is None:
            status = str(_("هدف پس از تکمیل اطلاعات"))
        elif progress == 100:
            status = str(_("به هدف نزدیک"))
        elif progress and progress > 0:
            status = str(_("در مسیر هدف"))
        else:
            status = str(_("شروع مسیر"))
        goal_metrics.append(
            {
                "key": key,
                "label": label,
                "current": current,
                "target": target,
                "current_display": goal_display(current, unit, decimals),
                "target_display": goal_display(target, unit, decimals),
                "progress_pct": progress or 0,
                "status": status,
                "unit": unit,
            }
        )

    chronological_rows = []
    for observation in observations:
        chronological_rows.append(
            {
                "date": observation["date"],
                "source": observation["source"],
                "weight": observation.get("weight"),
                "body_fat": observation.get("body_fat"),
                "lean_mass": observation.get("lean_mass"),
                "whr": observation.get("whr"),
            }
        )

    timeline_rows = []
    for row in reversed(chronological_rows):
        timeline_rows.append(
            {
                "date": row["date"],
                "source": row["source"],
                "weight": goal_display(row["weight"], "kg"),
                "body_fat": goal_display(row["body_fat"], "%"),
                "lean_mass": goal_display(row["lean_mass"], "kg"),
                "whr": goal_display(row["whr"], "", 2),
            }
        )

    chart_specs = [
        ("weight", str(_("وزن (kg)")), "kg", target_weight),
        ("body_fat", str(_("درصد چربی (%)")), "%", target_body_fat),
        ("lean_mass", str(_("تودهٔ بدون چربی (kg)")), "kg", target_lean_mass),
        ("whr", "WHR", "", target_whr),
    ]
    goal_charts = []
    for key, label, unit, target in chart_specs:
        values = [
            round(float(row[key]), 2 if key == "whr" else 1) if row.get(key) is not None else None
            for row in chronological_rows
        ]
        if not any(value is not None for value in values):
            continue
        goal_charts.append(
            {
                "key": key,
                "label": label,
                "unit": unit,
                "labels": [row["date"] for row in chronological_rows],
                "values": values,
                "target": target,
                "target_values": [target if value is not None else None for value in values]
                if target is not None
                else [],
            }
        )

    formula_names = [row["formula_name"] for row in observations if row.get("formula_name")]
    formula_note = formula_names[-1] if formula_names else None
    target_note = str(
        _("این هدف یک نقطهٔ شروع تخمینی است؛ با ثبت تست‌های بعدی هر ۴ تا ۶ هفته، مسیر و هدف باید بازبینی شود.")
    )
    if formula_note:
        target_note = f"{target_note} {str(_('مبنای درصد چربی'))}: {formula_note}."

    return {
        "available": True,
        "goal_type": goal_type,
        "goal_type_label": goal_titles[goal_type],
        "title": goal_titles[goal_type],
        "description": goal_descriptions[goal_type],
        "timeframe_weeks": timeframe_weeks,
        "metrics": goal_metrics,
        "timeline_rows": timeline_rows,
        "charts": goal_charts,
        "has_chart_data": any(sum(value is not None for value in chart["values"]) >= 2 for chart in goal_charts),
        "has_timeline": bool(timeline_rows),
        "note": target_note,
        "current_date": timeline_rows[0]["date"] if timeline_rows else None,
    }
