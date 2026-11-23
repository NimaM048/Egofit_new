"""Pair and interpret athlete measurements and build their metric series."""

from __future__ import annotations

from typing import Any

from django.utils.translation import gettext_lazy as _

from account.domain.body_composition.calculator import (
    CaliperSkinfolds,
    CircumferenceMeasures,
    build_body_composition_snapshot,
    calculate_bmi,
    calculate_lean_mass,
    calculate_whr,
    calculate_whtr,
    resolve_body_fat_result,
)

from .dates import (
    parse_jalali_date_obj,
    resolve_analysis_date_range,
    resolve_age,
    age_at_measurement,
    measurement_datetime,
    record_jalali_date,
)
from .presentation import ANALYSIS_METRIC_OPTIONS, metric_round, metric_unit


CIRCUMFERENCE_FIELD_LABELS = [
    ("wrist_cm", _("دور مچ دست")),
    ("forearm_cm", _("دور ساعد")),
    ("arm_rest_cm", _("دور بازو (استراحت)")),
    ("arm_flexed_cm", _("دور بازو (انقباض)")),
    ("wrist_left_cm", _("دور مچ دست (چپ)")),
    ("forearm_left_cm", _("دور ساعد (چپ)")),
    ("arm_rest_left_cm", _("دور بازو در حالت استراحت (چپ)")),
    ("arm_flexed_left_cm", _("دور بازو در حالت انقباض (چپ)")),
    ("head_cm", _("دور سر")),
    ("neck_cm", _("دور گردن")),
    ("chest_cm", _("دور قفسه سینه")),
    ("shoulders_cm", _("دور شانه‌ها")),
    ("waist_cm", _("دور کمر")),
    ("abdomen_cm", _("دور شکم")),
    ("hips_cm", _("دور باسن")),
    ("thigh_cm", _("دور ران")),
    ("calf_cm", _("دور ساق پا")),
    ("thigh_left_cm", _("دور ران پا (چپ)")),
    ("calf_left_cm", _("دور ساق پا (چپ)")),
    ("sit_height_cm", _("قد نشسته")),
    ("leg_length_cm", _("طول پا")),
]


CALIPER_FIELD_LABELS = [
    ("chest_armpit_men_mm", _("سینه")),
    ("axilla_mm", _("زیربغل")),
    ("subscapular_mm", _("تحت کتفی")),
    ("abdominal_mm", _("شکم")),
    ("suprailiac_mm", _("سه‌تیغ خاصره")),
    ("chest_mm", _("کمر")),
    ("biceps_mm", _("جلوبازو")),
    ("triceps_mm", _("پشت بازو")),
    ("thigh_mm", _("جلوی ران")),
    ("calf_mm", _("پشت ساق")),
]


BODY_FAT_FORMULA_OPTIONS = [
    {"key": "auto", "label": _("خودکار (بهترین فرمول موجود)")},
    {"key": "jp7", "label": "Jackson-Pollock 7-site"},
    {"key": "jp4", "label": "Jackson-Pollock 4-site"},
    {"key": "jp3", "label": "Jackson-Pollock 3-site"},
    {"key": "circumference", "label": _("درصد چربی (دورسنجی — Navy)")},
]


def build_measurement_display(circumference, caliper) -> dict:
    circumference_rows = []
    caliper_rows = []

    if circumference:
        for field_name, label in CIRCUMFERENCE_FIELD_LABELS:
            value = getattr(circumference, field_name, None)
            if value not in (None, ""):
                circumference_rows.append({"label": label, "value": f"{value} {_('سانتی‌متر')}"})

    if caliper:
        for field_name, label in CALIPER_FIELD_LABELS:
            value = getattr(caliper, field_name, None)
            if value not in (None, ""):
                caliper_rows.append({"label": label, "value": f"{value} {_('میلی‌متر')}"})

    return {
        "circumference_rows": circumference_rows,
        "caliper_rows": caliper_rows,
        "has_measurements": bool(circumference_rows or caliper_rows),
    }


def find_caliper_for_datetime(user, dt, caliper_records=None):
    import jdatetime
    from django.utils import timezone

    target_local = timezone.localtime(dt)
    target_j = jdatetime.datetime.fromgregorian(datetime=target_local).date()
    records = caliper_records if caliper_records is not None else user.caliper_records.all()
    for record in records:
        record_local = timezone.localtime(measurement_datetime(record))
        record_j = jdatetime.datetime.fromgregorian(datetime=record_local).date()
        if record_j == target_j:
            return record
    return None


def find_circumference_for_datetime(user, dt, circumference_records=None):
    import jdatetime
    from django.utils import timezone

    target_local = timezone.localtime(dt)
    target_j = jdatetime.datetime.fromgregorian(datetime=target_local).date()
    records = (
        circumference_records
        if circumference_records is not None
        else user.circumference_records.all()
    )
    for record in records:
        record_local = timezone.localtime(measurement_datetime(record))
        record_j = jdatetime.datetime.fromgregorian(datetime=record_local).date()
        if record_j == target_j:
            return record
    return None


def body_fat_from_caliper(
    caliper,
    *,
    age: int | None,
    gender: str | None,
    formula: str | None = None,
    weight_kg=None,
    height_cm=None,
) -> float | None:
    skinfolds = CaliperSkinfolds.from_model(caliper)
    result = resolve_body_fat_result(
        skinfolds=skinfolds,
        circumference=None,
        height_cm=height_cm,
        age=age,
        gender=gender,
        formula=formula,
        weight_kg=weight_kg,
    )
    return result.body_fat_percent if result else None


def body_fat_from_measurements(
    *,
    caliper,
    circumference,
    height_cm,
    age: int | None,
    gender: str | None,
    formula: str | None = None,
    weight_kg=None,
) -> float | None:
    result = resolve_body_fat_result(
        skinfolds=CaliperSkinfolds.from_model(caliper),
        circumference=CircumferenceMeasures.from_model(circumference),
        height_cm=height_cm,
        age=age,
        gender=gender,
        formula=formula,
        weight_kg=weight_kg,
    )
    return result.body_fat_percent if result else None


def whr_from_circumference(circumference) -> float | None:
    measures = CircumferenceMeasures.from_model(circumference)
    if measures is None:
        return None
    return calculate_whr(measures.waist_cm, measures.hips_cm)


def whtr_from_circumference(user, circumference) -> float | None:
    measures = CircumferenceMeasures.from_model(circumference)
    if measures is None:
        return None
    return calculate_whtr(measures.waist_cm, user.height_cm)


def bmr_value(user, age) -> int | None:
    from account.domain.body_composition.calculator import calculate_bmr_mifflin_st_jeor

    return calculate_bmr_mifflin_st_jeor(
        weight_kg=user.weight_kg,
        height_cm=user.height_cm,
        age=age,
        gender=user.gender,
    )


def metric_value_from_records(
    user,
    metric: str,
    circumference,
    caliper,
    *,
    body_fat_formula: str | None = None,
):
    age = age_at_measurement(user, circumference or caliper)
    gender = user.gender
    weight = user.weight_kg
    height = user.height_cm
    if circumference is not None:
        if circumference.weight_kg is not None:
            weight = circumference.weight_kg
        if circumference.height_cm is not None:
            height = circumference.height_cm
    skinfolds = CaliperSkinfolds.from_model(caliper)
    measures = CircumferenceMeasures.from_model(circumference)
    snapshot = build_body_composition_snapshot(
        weight_kg=weight,
        height_cm=height,
        age=age,
        gender=gender,
        skinfolds=skinfolds,
        circumference=measures,
        body_fat_formula=body_fat_formula,
    )

    if metric == "body_fat":
        return snapshot.body_fat_percent
    if metric == "lean_mass":
        return snapshot.lean_mass_kg
    if metric == "whr":
        return snapshot.whr
    if metric == "whtr":
        return snapshot.whtr
    if metric == "bmi":
        return snapshot.bmi
    if metric == "bmr":
        return snapshot.bmr
    return snapshot.body_fat_percent


def metric_series_points(
    user,
    metric: str,
    start_j,
    end_j,
    *,
    body_fat_formula: str | None = None,
    circumference_records=None,
    caliper_records=None,
) -> list[tuple]:
    """Sorted ``(jalali_date, value)`` points for a metric within a Jalali date range."""
    series: list[tuple] = []
    age = resolve_age(user)
    gender = user.gender
    selected_body_fat_formula = resolve_body_fat_formula(body_fat_formula)
    circ_records = circumference_records if circumference_records is not None else user.circumference_records.all()
    cal_records = caliper_records if caliper_records is not None else user.caliper_records.all()

    if metric == "body_fat":
        if selected_body_fat_formula == "circumference":
            for record in circ_records:
                j_date = parse_jalali_date_obj(record_jalali_date(record))
                if start_j <= j_date <= end_j:
                    paired_caliper = find_caliper_for_datetime(user, measurement_datetime(record), caliper_records=cal_records)
                    weight = record.weight_kg if record.weight_kg is not None else user.weight_kg
                    height = record.height_cm if record.height_cm is not None else user.height_cm
                    value = body_fat_from_measurements(
                        caliper=paired_caliper,
                        circumference=record,
                        height_cm=height,
                        age=age_at_measurement(user, record),
                        gender=gender,
                        formula=selected_body_fat_formula,
                        weight_kg=weight,
                    )
                    if value is not None:
                        series.append((j_date, value))
        else:
            for record in cal_records:
                j_date = parse_jalali_date_obj(record_jalali_date(record))
                if start_j <= j_date <= end_j:
                    paired_circumference = find_circumference_for_datetime(
                        user,
                        measurement_datetime(record),
                        circumference_records=circ_records,
                    )
                    weight = (
                        paired_circumference.weight_kg
                        if paired_circumference is not None and paired_circumference.weight_kg is not None
                        else user.weight_kg
                    )
                    height = (
                        paired_circumference.height_cm
                        if paired_circumference is not None and paired_circumference.height_cm is not None
                        else user.height_cm
                    )
                    value = body_fat_from_caliper(
                        record,
                        age=age_at_measurement(user, record),
                        gender=gender,
                        formula=selected_body_fat_formula,
                        weight_kg=weight,
                        height_cm=height,
                    )
                    if value is not None:
                        series.append((j_date, value))
            for record in circ_records:
                measurement_age = age_at_measurement(user, record)
                if measurement_age is None or not 7 <= measurement_age < 18:
                    continue
                if find_caliper_for_datetime(
                    user,
                    measurement_datetime(record),
                    caliper_records=cal_records,
                ):
                    continue
                j_date = parse_jalali_date_obj(record_jalali_date(record))
                if start_j <= j_date <= end_j:
                    weight = record.weight_kg if record.weight_kg is not None else user.weight_kg
                    height = record.height_cm if record.height_cm is not None else user.height_cm
                    value = body_fat_from_measurements(
                        caliper=None,
                        circumference=record,
                        height_cm=height,
                        age=measurement_age,
                        gender=gender,
                        formula=selected_body_fat_formula,
                        weight_kg=weight,
                    )
                    if value is not None:
                        series.append((j_date, value))
    elif metric in ("whr", "whtr"):
        for record in circ_records:
            j_date = parse_jalali_date_obj(record_jalali_date(record))
            if start_j <= j_date <= end_j:
                value = (
                    whr_from_circumference(record)
                    if metric == "whr"
                    else whtr_from_circumference(user, record)
                )
                if value is not None:
                    series.append((j_date, value))
    elif metric == "lean_mass":
        if selected_body_fat_formula == "circumference":
            for record in circ_records:
                j_date = parse_jalali_date_obj(record_jalali_date(record))
                if start_j <= j_date <= end_j:
                    paired_caliper = find_caliper_for_datetime(user, measurement_datetime(record), caliper_records=cal_records)
                    weight = record.weight_kg if record.weight_kg is not None else user.weight_kg
                    height = record.height_cm if record.height_cm is not None else user.height_cm
                    body_fat = body_fat_from_measurements(
                        caliper=paired_caliper,
                        circumference=record,
                        height_cm=height,
                        age=age_at_measurement(user, record),
                        gender=gender,
                        formula=selected_body_fat_formula,
                        weight_kg=weight,
                    )
                    value = calculate_lean_mass(weight, body_fat)
                    if value is not None:
                        series.append((j_date, value))
        else:
            for record in cal_records:
                j_date = parse_jalali_date_obj(record_jalali_date(record))
                if start_j <= j_date <= end_j:
                    paired_circumference = find_circumference_for_datetime(
                        user,
                        measurement_datetime(record),
                        circumference_records=circ_records,
                    )
                    weight = (
                        paired_circumference.weight_kg
                        if paired_circumference is not None and paired_circumference.weight_kg is not None
                        else user.weight_kg
                    )
                    height = (
                        paired_circumference.height_cm
                        if paired_circumference is not None and paired_circumference.height_cm is not None
                        else user.height_cm
                    )
                    body_fat = body_fat_from_caliper(
                        record,
                        age=age_at_measurement(user, record),
                        gender=gender,
                        formula=selected_body_fat_formula,
                        weight_kg=weight,
                        height_cm=height,
                    )
                    value = calculate_lean_mass(weight, body_fat)
                    if value is not None:
                        series.append((j_date, value))
            for record in circ_records:
                measurement_age = age_at_measurement(user, record)
                if measurement_age is None or not 7 <= measurement_age < 18:
                    continue
                if find_caliper_for_datetime(
                    user,
                    measurement_datetime(record),
                    caliper_records=cal_records,
                ):
                    continue
                j_date = parse_jalali_date_obj(record_jalali_date(record))
                if start_j <= j_date <= end_j:
                    weight = record.weight_kg if record.weight_kg is not None else user.weight_kg
                    height = record.height_cm if record.height_cm is not None else user.height_cm
                    body_fat = body_fat_from_measurements(
                        caliper=None,
                        circumference=record,
                        height_cm=height,
                        age=measurement_age,
                        gender=gender,
                        formula=selected_body_fat_formula,
                        weight_kg=weight,
                    )
                    value = calculate_lean_mass(weight, body_fat)
                    if value is not None:
                        series.append((j_date, value))
    elif metric == "bmi":
        for record in circ_records:
            j_date = parse_jalali_date_obj(record_jalali_date(record))
            if start_j <= j_date <= end_j:
                weight = record.weight_kg
                height = record.height_cm
                bmi = calculate_bmi(weight, height)
                if bmi is not None:
                    series.append((j_date, bmi))
    elif metric == "bmr":
        bmr = bmr_value(user, age)
        if bmr is not None:
            for record in circ_records:
                j_date = parse_jalali_date_obj(record_jalali_date(record))
                if start_j <= j_date <= end_j:
                    series.append((j_date, bmr))
    else:
        for record in circ_records:
            j_date = parse_jalali_date_obj(record_jalali_date(record))
            if start_j <= j_date <= end_j:
                paired_caliper = find_caliper_for_datetime(user, measurement_datetime(record), caliper_records=cal_records)
                value = metric_value_from_records(
                    user,
                    metric,
                    record,
                    paired_caliper,
                    body_fat_formula=selected_body_fat_formula,
                )
                if value is not None:
                    series.append((j_date, value))

    series.sort(key=lambda item: item[0])
    return series


def collect_metric_series(
    user,
    metric: str,
    start_j,
    end_j,
    *,
    body_fat_formula: str | None = None,
    circumference_records=None,
    caliper_records=None,
) -> list[float]:
    return [
        float(value)
        for _, value in metric_series_points(
            user,
            metric,
            start_j,
            end_j,
            body_fat_formula=body_fat_formula,
            circumference_records=circumference_records,
            caliper_records=caliper_records,
        )
    ]


def resolve_body_fat_formula(value: str | None) -> str:
    valid = {option["key"] for option in BODY_FAT_FORMULA_OPTIONS}
    formula = (value or "auto").strip().lower()
    return formula if formula in valid else "auto"


def body_fat_formula_label(formula_key: str) -> str:
    for option in BODY_FAT_FORMULA_OPTIONS:
        if option["key"] == formula_key:
            return option["label"]
    return formula_key


def body_composition_observations(
    user: Any,
    *,
    circumference_records,
    caliper_records,
    body_fat_formula: str,
) -> list[dict[str, Any]]:
    """Build dated body-fat and lean-mass observations from available tests."""
    gender = user.gender

    def canonical_date(record):
        try:
            parsed = parse_jalali_date_obj(record_jalali_date(record))
            return parsed.strftime("%Y/%m/%d")
        except (TypeError, ValueError, IndexError):
            return ""

    def records_by_date(records):
        dated_records = {}
        for record in reversed(list(records)):
            label = canonical_date(record)
            if label:
                dated_records[label] = record
        return dated_records

    circumferences_by_date = records_by_date(circumference_records)
    calipers_by_date = records_by_date(caliper_records)
    observations: dict[str, dict[str, Any]] = {}

    def add_observation(label, circumference=None, caliper=None):
        if not label:
            return
        measurement_record = circumference or caliper
        age = age_at_measurement(user, measurement_record)
        height = (
            circumference.height_cm
            if circumference is not None and circumference.height_cm is not None
            else user.height_cm
        )
        weight = (
            circumference.weight_kg
            if circumference is not None and circumference.weight_kg is not None
            else user.weight_kg
        )
        if circumference is not None:
            body_fat = body_fat_from_measurements(
                caliper=caliper,
                circumference=circumference,
                height_cm=height,
                age=age,
                gender=gender,
                formula=body_fat_formula,
                weight_kg=weight,
            )
            if body_fat is None and body_fat_formula == "auto":
                body_fat = body_fat_from_measurements(
                    caliper=None,
                    circumference=circumference,
                    height_cm=height,
                    age=age,
                    gender=gender,
                    formula="circumference",
                    weight_kg=weight,
                )
        elif body_fat_formula == "circumference" and not (age is not None and age < 18):
            body_fat = None
        else:
            body_fat = body_fat_from_caliper(
                caliper,
                age=age,
                gender=gender,
                formula=body_fat_formula,
                weight_kg=weight,
                height_cm=height,
            )
        if body_fat is None:
            return
        try:
            sort_key = parse_jalali_date_obj(label)
        except (TypeError, ValueError, IndexError):
            return
        observations[label] = {
            "date": label,
            "_sort_key": sort_key,
            "body_fat": round(float(body_fat), 1),
            "lean_mass": calculate_lean_mass(weight, body_fat),
        }

    for label, circumference in circumferences_by_date.items():
        add_observation(
            label,
            circumference=circumference,
            caliper=calipers_by_date.get(label),
        )
    for label, caliper in calipers_by_date.items():
        if label not in circumferences_by_date:
            add_observation(label, caliper=caliper)

    return sorted(observations.values(), key=lambda row: row["_sort_key"])


def get_analysis_metric_series(
    user: Any,
    *,
    body_fat_formula: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    """Return normalized trend-series data for the analysis charts."""
    """Per-metric labelled time series for the client-side metric dropdown chart."""
    start_date, end_date, start_j, end_j = resolve_analysis_date_range(user, start, end)
    selected_body_fat_formula = resolve_body_fat_formula(body_fat_formula)
    series: dict = {}
    for option in ANALYSIS_METRIC_OPTIONS:
        key = option["key"]
        points = metric_series_points(
            user,
            key,
            start_j,
            end_j,
            body_fat_formula=selected_body_fat_formula,
        )
        series[key] = {
            "labels": [point[0].strftime("%Y/%m/%d") for point in points],
            "values": [metric_round(key, point[1]) for point in points],
            "label": str(option["label"]),
            "unit": metric_unit(key),
        }
    return series
