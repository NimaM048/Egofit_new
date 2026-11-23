"""Assemble the detailed athlete analysis page context."""

from __future__ import annotations

from typing import Any

from django.utils.translation import gettext_lazy as _

from account.domain.body_composition.calculator import (
    CaliperSkinfolds,
    CircumferenceMeasures,
    bmi_category_label,
    build_body_composition_snapshot,
    calculate_bmi,
    resolve_body_fat_result,
)

from .dates import (
    validate_jalali_date,
    parse_jalali_date_obj,
    default_analysis_date_range,
    analysis_date_options,
    resolve_analysis_date_range,
    resolve_age,
    age_at_measurement,
    measurement_datetime,
    record_jalali_date,
)
from .presentation import (
    ANALYSIS_METRIC_OPTIONS,
    analysis_metric_label,
    segmented_bar,
    build_chart,
    status_tone,
    tile_number,
    scale_series_for_chart,
    format_metric_display,
)
from .measurements import (
    BODY_FAT_FORMULA_OPTIONS,
    build_measurement_display,
    find_caliper_for_datetime,
    whr_from_circumference,
    metric_value_from_records,
    collect_metric_series,
    resolve_body_fat_formula,
    body_fat_formula_label,
)


def get_analysis_context(
    user,
    *,
    metric: str | None = None,
    start: str | None = None,
    end: str | None = None,
    date: str | None = None,
    circ_date: str | None = None,
    body_fat_formula: str | None = None,
    caliper_date: str | None = None,
    use_full_chart_range: bool = False,
) -> dict[str, Any]:
    """Build the selected body-composition analysis context for the profile page."""
    valid_metrics = {option["key"] for option in ANALYSIS_METRIC_OPTIONS}
    selected_metric = metric if metric in valid_metrics else "body_fat"
    if use_full_chart_range and not start and not end:
        chart_start_date, chart_end_date = default_analysis_date_range(user)
        start_j = parse_jalali_date_obj(chart_start_date)
        end_j = parse_jalali_date_obj(chart_end_date)
    else:
        chart_start_date, chart_end_date, start_j, end_j = resolve_analysis_date_range(
            user,
            start,
            end,
        )

    bmi = calculate_bmi(user.weight_kg, user.height_cm)
    bmi_label = bmi_category_label(bmi)
    age = resolve_age(user)
    has_data = any(
        value not in (None, "")
        for value in (user.weight_kg, user.height_cm, user.birth_date_jalali, user.blood_group, user.gender)
    ) or user.circumference_records.exists() or user.caliper_records.exists()

    circumference_records = list(user.circumference_records.all()[:120])
    caliper_records = list(user.caliper_records.all()[:120])
    circ_by_date: dict = {}
    for record in sorted(circumference_records, key=measurement_datetime, reverse=True):
        circ_by_date.setdefault(record_jalali_date(record), record)
    cal_by_date: dict = {}
    for record in sorted(caliper_records, key=measurement_datetime, reverse=True):
        cal_by_date.setdefault(record_jalali_date(record), record)

    circ_dates = sorted(circ_by_date.keys(), reverse=True)
    analysis_circ_dates = [{"value": value, "label": value} for value in circ_dates]
    selected_body_fat_formula = resolve_body_fat_formula(body_fat_formula)
    analysis_body_fat_formulas = list(BODY_FAT_FORMULA_OPTIONS)
    if age is not None and age < 18:
        selected_body_fat_formula = "auto"

    legacy_date = validate_jalali_date(date)
    selected_circ_date = validate_jalali_date(circ_date) or legacy_date
    if selected_circ_date not in circ_dates:
        selected_circ_date = circ_dates[0] if circ_dates else None

    caliper_dates = sorted(cal_by_date.keys(), reverse=True)
    selected_date = selected_circ_date or (caliper_dates[0] if caliper_dates else None)

    latest_circumference = circ_by_date.get(selected_circ_date) or (circumference_records[0] if circumference_records else None)
    paired_caliper = (
        find_caliper_for_datetime(user, measurement_datetime(latest_circumference), caliper_records=caliper_records)
        if latest_circumference
        else (caliper_records[0] if caliper_records else None)
    ) or (caliper_records[0] if caliper_records else None)

    skinfolds = CaliperSkinfolds.from_model(paired_caliper)
    measures = CircumferenceMeasures.from_model(latest_circumference)
    snapshot_weight = latest_circumference.weight_kg if latest_circumference and latest_circumference.weight_kg is not None else user.weight_kg
    snapshot_height = latest_circumference.height_cm if latest_circumference and latest_circumference.height_cm is not None else user.height_cm
    snapshot_age = age_at_measurement(user, latest_circumference or paired_caliper)
    if snapshot_age is not None and snapshot_age < 18:
        selected_body_fat_formula = "auto"
    composition = build_body_composition_snapshot(
        weight_kg=snapshot_weight,
        height_cm=snapshot_height,
        age=snapshot_age,
        gender=user.gender,
        skinfolds=skinfolds,
        circumference=measures,
        activity_level=user.activity_level,
        body_fat_formula=selected_body_fat_formula,
    )
    body_fat_result = resolve_body_fat_result(
        skinfolds=skinfolds,
        circumference=measures,
        height_cm=snapshot_height,
        age=snapshot_age,
        gender=user.gender,
        formula=selected_body_fat_formula,
        weight_kg=snapshot_weight,
    )
    measurements = build_measurement_display(latest_circumference, paired_caliper)

    raw_series = collect_metric_series(
        user,
        selected_metric,
        start_j,
        end_j,
        body_fat_formula=selected_body_fat_formula,
        circumference_records=circumference_records,
        caliper_records=caliper_records,
    )
    chart_has_real_data = len(raw_series) >= 2
    if chart_has_real_data:
        chart_input = scale_series_for_chart(selected_metric, raw_series)
        chart_points, chart_path, chart_area_path = build_chart(chart_input)
    else:
        chart_points, chart_path, chart_area_path = [], "", ""

    body_fat = composition.body_fat_percent
    current_value = metric_value_from_records(
        user,
        selected_metric,
        latest_circumference,
        paired_caliper,
        body_fat_formula=selected_body_fat_formula,
    )
    analysis_value = format_metric_display(selected_metric, current_value)
    bmr_display = composition.bmr if composition.bmr is not None else _("ثبت نشده")
    daily_calories = composition.tdee if composition.tdee is not None else _("ثبت نشده")
    if composition.protein_min_g is not None and composition.protein_max_g is not None:
        daily_protein = _("%(min)s تا %(max)s گرم") % {
            "min": composition.protein_min_g,
            "max": composition.protein_max_g,
        }
    else:
        daily_protein = _("ثبت نشده")

    if not user.gender and selected_metric == "body_fat":
        analysis_subtitle = _("برای محاسبه درصد چربی، مربی باید جنسیت را در پروفایل ثبت کند.")
    elif body_fat is None and selected_metric == "body_fat":
        if snapshot_age is not None and snapshot_age < 7:
            analysis_subtitle = _("برای سن کمتر از ۷ سال، این سامانه فرمول برآورد معتبر ندارد.")
        elif selected_body_fat_formula == "circumference":
            analysis_subtitle = _("برای محاسبه درصد چربی دورسنجی، دور کمر، گردن و قد لازم است.")
        else:
            analysis_subtitle = _("برای محاسبه درصد چربی، اندازه‌گیری کالیپر کافی توسط مربی لازم است.")
    elif snapshot_age is not None and 7 <= snapshot_age < 18 and selected_metric == "body_fat":
        analysis_subtitle = _(
            "برآورد سنین زیر ۱۸ سال از BMI، سن و جنسیت با فرمول Deurenberg انجام می‌شود؛ خطای برآورد در کودکان بیشتر است."
        )
    elif bmi is not None:
        analysis_subtitle = bmi_label
    else:
        analysis_subtitle = _("برای نمایش گزارش، وزن و قد را ثبت کنید.")

    progress = int(current_value) if isinstance(current_value, (int, float)) else (body_fat or 0)
    if selected_metric in ("whr", "whtr") and isinstance(current_value, float):
        progress = int(current_value * 100)
    elif selected_metric == "bmi" and isinstance(current_value, float):
        progress = int(min(current_value * 2.2, 96))

    filter_label = analysis_metric_label(selected_metric)
    if use_full_chart_range and chart_has_real_data:
        range_label = _("روند کامل اندازه‌گیری‌ها")
    else:
        range_label = _("از تاریخ %(start_date)s تا تاریخ %(end_date)s") % {
            "start_date": chart_start_date,
            "end_date": chart_end_date,
        }

    # --- Calculation result tiles (clean numeric squares; no somatotype) ----
    whr_value = composition.whr if composition.whr is not None else (
        whr_from_circumference(latest_circumference) if latest_circumference else None
    )
    analysis_result_tiles = [
        {
            "key": "body_fat",
            "label": _("درصد چربی بدن"),
            "value": tile_number(body_fat),
            "unit": "%",
            "tone": "neutral",
            "status": "",
        },
        {
            "key": "lean_mass",
            "label": _("توده بدون چربی"),
            "value": tile_number(composition.lean_mass_kg),
            "unit": "kg",
            "tone": "neutral",
            "status": "",
        },
        {
            "key": "whr",
            "label": "WHR",
            "value": tile_number(whr_value, decimals=2),
            "unit": "",
            "tone": status_tone(composition.whr_status),
            "status": composition.whr_status or "",
        },
        {
            "key": "whtr",
            "label": "WHtR",
            "value": tile_number(composition.whtr, decimals=2),
            "unit": "",
            "tone": status_tone(composition.whtr_status),
            "status": composition.whtr_status or "",
        },
        {
            "key": "bmi",
            "label": "BMI",
            "value": bmi if bmi is not None else "—",
            "unit": "",
            "tone": status_tone(bmi_label) if bmi is not None else "neutral",
            "status": bmi_label if bmi is not None else "",
        },
        {
            "key": "bmr",
            "label": "BMR",
            "value": tile_number(composition.bmr, decimals=0),
            "unit": "kcal",
            "tone": "neutral",
            "status": "",
        },
        {
            "key": "calories",
            "label": _("کالری روزانه"),
            "value": tile_number(composition.tdee, decimals=0),
            "unit": "kcal",
            "tone": "neutral",
            "status": "",
        },
        {
            "key": "protein",
            "label": _("پروتئین روزانه"),
            "value": daily_protein,
            "unit": "",
            "tone": "neutral",
            "status": "",
        },
    ]

    analysis_range_dates = analysis_date_options(user)
    for value in (chart_start_date, chart_end_date):
        if value not in analysis_range_dates:
            analysis_range_dates.append(value)
    analysis_range_dates.sort(key=parse_jalali_date_obj, reverse=True)

    return {
        "analysis_has_data": has_data,
        "analysis_selected_metric": selected_metric,
        "analysis_filter_label": filter_label,
        "analysis_metric_options": ANALYSIS_METRIC_OPTIONS,
        "analysis_range_label": range_label,
        "analysis_start_date": chart_start_date,
        "analysis_end_date": chart_end_date,
        "analysis_range_dates": [{"value": value, "label": value} for value in analysis_range_dates],
        "analysis_selected_start_date": chart_start_date,
        "analysis_selected_end_date": chart_end_date,
        "analysis_dates": [{"value": value, "label": value} for value in circ_dates],
        "analysis_selected_date": selected_date,
        "analysis_circ_dates": analysis_circ_dates,
        "analysis_selected_circ_date": selected_circ_date,
        "analysis_body_fat_formulas": analysis_body_fat_formulas,
        "analysis_selected_body_fat_formula": selected_body_fat_formula,
        "analysis_selected_body_fat_formula_label": (
            body_fat_result.formula_name
            if snapshot_age is not None and snapshot_age < 18 and body_fat_result
            else body_fat_formula_label(selected_body_fat_formula)
        ),
        "analysis_result_tiles": analysis_result_tiles,
        "analysis_summary": {
            "title": filter_label,
            "value": analysis_value,
            "progress": progress,
            "subtitle": analysis_subtitle,
        },
        "analysis_chart_has_data": chart_has_real_data,
        "analysis_formula_used": body_fat_result.formula_name if body_fat_result else composition.body_fat_formula,
        "analysis_measurements": measurements,
        "analysis_body_composition": {
            "fat_mass_kg": composition.fat_mass_kg,
            "lean_mass_kg": composition.lean_mass_kg,
            "tdee": composition.tdee,
            "calories_for_loss": composition.calories_for_loss,
            "calories_for_gain": composition.calories_for_gain,
            "protein_min_g": composition.protein_min_g,
            "protein_max_g": composition.protein_max_g,
            "whr_status": composition.whr_status,
            "whtr_status": composition.whtr_status,
            "body_frame_size": composition.body_frame_size,
            "endomorphy": composition.endomorphy,
            "mesomorphy": composition.mesomorphy,
            "ectomorphy": composition.ectomorphy,
            "arm_ratio": composition.arm_ratio,
            "shoulder_hip_ratio": composition.shoulder_hip_ratio,
        },
        "analysis_metrics": [
            {
                "label": _("درصد چربی بدن"),
                "value": format_metric_display("body_fat", body_fat),
                **segmented_bar(int(body_fat) if body_fat else 18),
            },
            {
                "label": _("توده عضلانی بدون چربی"),
                "value": format_metric_display(
                    "lean_mass",
                    composition.lean_mass_kg,
                ),
                **segmented_bar(58 if user.weight_kg else 16),
            },
            {
                "label": "WHR",
                "value": format_metric_display(
                    "whr",
                    whr_from_circumference(latest_circumference) if latest_circumference else None,
                ),
                **segmented_bar(
                    int((whr_from_circumference(latest_circumference) or 0.46) * 100)
                    if latest_circumference
                    else 14
                ),
            },
            {
                "label": _("وضعیت WHR"),
                "value": composition.whr_status or _("ثبت نشده"),
                **segmented_bar(int((composition.whr or 0.46) * 100)),
            },
            {
                "label": _("وضعیت WHtR"),
                "value": composition.whtr_status or _("ثبت نشده"),
                **segmented_bar(int((composition.whtr or 0.46) * 100)),
            },
            {
                "label": _("اندازه قاب بدن"),
                "value": composition.body_frame_size or _("ثبت نشده"),
                **segmented_bar(18),
            },
            {
                "label": _("سوماتوتایپ (اندومورف/مزومورف/اکتومورف)"),
                "value": (
                    f"{composition.endomorphy} / {composition.mesomorphy} / {composition.ectomorphy}"
                    if composition.endomorphy is not None
                    else _("ثبت نشده")
                ),
                **segmented_bar(20),
            },
            {
                "label": "BMI",
                "value": bmi if bmi is not None else _("ثبت نشده"),
                **segmented_bar(round(min(bmi or 0, 40) * 2.2) if bmi is not None else 18),
            },
            {
                "label": "BMR",
                "value": bmr_display,
                **segmented_bar(42 if bmi is not None else 18),
            },
            {
                "label": _("کالری مورد نیاز روزانه"),
                "value": daily_calories,
                **segmented_bar(22 if bmi is not None else 14),
            },
            {
                "label": _("پروتئین مورد نیاز روزانه"),
                "value": daily_protein,
                **segmented_bar(16 if user.weight_kg else 14),
            },
        ],
        "analysis_chart_points": chart_points,
        "analysis_chart_path": chart_path,
        "analysis_chart_area_path": chart_area_path,
        "analysis_age": age,
        "analysis_bmi": bmi,
    }
