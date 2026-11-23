"""Assemble analysis dashboard panels and body composition summaries."""

from __future__ import annotations

from typing import Any

from django.utils.translation import gettext_lazy as _

from account.domain.body_composition.calculator import (
    activity_factor_for_level,
    bmi_category_label,
    calculate_bmi,
    calculate_bmr_mifflin_st_jeor,
    calculate_lean_mass,
    calculate_tdee,
    calculate_whr_status,
)

from .dates import (
    parse_jalali_date_obj,
    resolve_analysis_date_range,
    resolve_age,
    age_at_measurement,
    measurement_datetime,
    record_jalali_date,
)
from .measurements import (
    find_caliper_for_datetime,
    find_circumference_for_datetime,
    body_fat_from_caliper,
    body_fat_from_measurements,
    whr_from_circumference,
    metric_series_points,
    resolve_body_fat_formula,
    body_composition_observations,
)
from .goals import build_smart_goal_data


def get_dashboard_body_composition_data(
    user: Any,
    *,
    body_fat_formula: str | None = None,
) -> dict[str, Any]:
    """Return the user's body-composition trend and same-gender site average."""
    from django.db.models import OuterRef, Q, Subquery

    from account.models import BodyCircumferenceMeasurement, CaliperMeasurement, User

    formula = resolve_body_fat_formula(body_fat_formula)
    circumference_records = list(user.circumference_records.all()[:120])
    caliper_records = list(user.caliper_records.all()[:120])
    observations = body_composition_observations(
        user,
        circumference_records=circumference_records,
        caliper_records=caliper_records,
        body_fat_formula=formula,
    )
    labels = [row["date"] for row in observations]
    body_fat_values = [row["body_fat"] for row in observations]
    lean_mass_values = [row["lean_mass"] for row in observations]

    peer_values: list[float] = []
    user_age = resolve_age(user)
    if user.gender in User.GenderChoices.values and body_fat_values and user_age is not None and user_age >= 18:
        latest_circumference_id = Subquery(
            BodyCircumferenceMeasurement.objects.filter(user_id=OuterRef("pk"))
            .order_by("-measured_at", "-pk")
            .values("pk")[:1]
        )
        latest_caliper_id = Subquery(
            CaliperMeasurement.objects.filter(user_id=OuterRef("pk"))
            .order_by("-measured_at", "-pk")
            .values("pk")[:1]
        )
        peers = list(
            User.objects.filter(
                is_active=True,
                is_admin=False,
                gender=user.gender,
            )
            .annotate(
                latest_circumference_id=latest_circumference_id,
                latest_caliper_id=latest_caliper_id,
            )
            .filter(
                Q(latest_circumference_id__isnull=False)
                | Q(latest_caliper_id__isnull=False)
            )
            .only("id", "gender", "age", "birth_date_jalali", "height_cm", "weight_kg")
        )

        def get_latest_records(model, primary_keys):
            records_by_user = {}
            for offset in range(0, len(primary_keys), 500):
                batch = primary_keys[offset:offset + 500]
                records_by_user.update(
                    {
                        record.user_id: record
                        for record in model.objects.filter(pk__in=batch)
                    }
                )
            return records_by_user

        circumference_by_user = get_latest_records(
            BodyCircumferenceMeasurement,
            [peer.latest_circumference_id for peer in peers if peer.latest_circumference_id],
        )
        caliper_by_user = get_latest_records(
            CaliperMeasurement,
            [peer.latest_caliper_id for peer in peers if peer.latest_caliper_id],
        )
        for peer in peers:
            peer_observations = body_composition_observations(
                peer,
                circumference_records=[circumference_by_user[peer.pk]]
                if peer.pk in circumference_by_user
                else [],
                caliper_records=[caliper_by_user[peer.pk]]
                if peer.pk in caliper_by_user
                else [],
                body_fat_formula=formula,
            )
            if peer_observations:
                peer_values.append(peer_observations[-1]["body_fat"])

    latest_body_fat = body_fat_values[-1] if body_fat_values else None
    comparison_available = latest_body_fat is not None and bool(peer_values)
    comparison_values = (
        [latest_body_fat, round(sum(peer_values) / len(peer_values), 1)]
        if comparison_available
        else []
    )
    return {
        "comparison": {
            "available": comparison_available,
            "labels": [str(_("شما")), str(_("میانگین کاربران هم‌جنس"))]
            if comparison_available
            else [],
            "values": comparison_values,
            "users_count": len(peer_values),
            "latest_body_fat": latest_body_fat,
        },
        "progress": {
            "available": bool(body_fat_values),
            "labels": labels,
            "body_fat": body_fat_values,
            "lean_mass": lean_mass_values,
        },
    }


def get_analysis_dashboard_data(
    user: Any,
    *,
    body_fat_formula: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    """Build the dashboard cards, history rows, and gauges for body analysis."""
    """JSON-ready time-series + gauges + history rows for the analysis dashboard (Chart.js)."""
    age = resolve_age(user)
    gender = user.gender
    activity_factor = activity_factor_for_level(user.activity_level)
    selected_body_fat_formula = resolve_body_fat_formula(body_fat_formula)

    circ_records_all = sorted(user.circumference_records.all()[:120], key=measurement_datetime)
    cal_records_all = sorted(user.caliper_records.all()[:120], key=measurement_datetime)
    range_start, range_end, start_j, end_j = resolve_analysis_date_range(user, start, end)

    def in_range(record):
        value = parse_jalali_date_obj(record_jalali_date(record))
        return start_j <= value <= end_j

    circ_records = [record for record in circ_records_all if in_range(record)]
    cal_records = [record for record in cal_records_all if in_range(record)]

    def jdate(record) -> str:
        return record_jalali_date(record)

    circ_labels = [jdate(record) for record in circ_records]

    def aligned(attr):
        out = []
        for record in circ_records:
            value = getattr(record, attr, None)
            out.append(float(value) if value not in (None, "") else None)
        return out

    # --- Main trend charts -------------------------------------------------
    weight_labels, weight_values = [], []
    bmi_labels, bmi_values = [], []
    whr_labels, whr_values = [], []
    cal_labels, cal_values = [], []
    for record in circ_records:
        label = jdate(record)
        weight = record.weight_kg
        height = record.height_cm
        if weight:
            weight_labels.append(label)
            weight_values.append(int(weight))
        bmr = calculate_bmr_mifflin_st_jeor(weight_kg=weight, height_cm=height, age=age, gender=gender)
        tdee = calculate_tdee(bmr, activity_factor)
        if tdee is not None:
            cal_labels.append(label)
            cal_values.append(tdee)

    bmi_points = metric_series_points(
        user,
        "bmi",
        start_j,
        end_j,
        circumference_records=circ_records,
        caliper_records=cal_records,
    )
    bmi_labels = [point_date.strftime("%Y/%m/%d") for point_date, _ in bmi_points]
    bmi_values = [value for _, value in bmi_points]
    whr_points = metric_series_points(
        user,
        "whr",
        start_j,
        end_j,
        circumference_records=circ_records,
        caliper_records=cal_records,
    )
    whr_labels = [point_date.strftime("%Y/%m/%d") for point_date, _ in whr_points]
    whr_values = [round(value, 2) for _, value in whr_points]

    fat_labels, fat_values = [], []
    if selected_body_fat_formula == "circumference":
        for record in circ_records:
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
                fat_labels.append(jdate(record))
                fat_values.append(round(float(value), 1))
    else:
        for record in cal_records:
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
                fat_labels.append(jdate(record))
                fat_values.append(round(float(value), 1))
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
                fat_labels.append(jdate(record))
                fat_values.append(round(float(value), 1))

    trend_charts = {
        "weight": {"labels": weight_labels, "values": weight_values},
        "bmi": {"labels": bmi_labels, "values": bmi_values},
        "whr": {"labels": whr_labels, "values": whr_values},
        "body_fat": {"labels": fat_labels, "values": fat_values},
        "calories": {"labels": cal_labels, "values": cal_values},
    }

    # --- Selectable measurement-trend groups (shared circumference x-axis) --
    measurement_groups = {
        "chest-shoulder": {
            "labels": circ_labels,
            "datasets": [
                {"label": str(_("دور سینه")), "data": aligned("chest_cm")},
                {"label": str(_("دور سرشانه")), "data": aligned("shoulders_cm")},
            ],
        },
        "mid-body": {
            "labels": circ_labels,
            "datasets": [
                {"label": str(_("دور شکم")), "data": aligned("abdomen_cm")},
                {"label": str(_("دور کمر")), "data": aligned("waist_cm")},
                {"label": str(_("دور باسن")), "data": aligned("hips_cm")},
            ],
        },
        "arms": {
            "labels": circ_labels,
            "datasets": [
                {"label": str(_("بازو آزاد")), "data": aligned("arm_rest_cm")},
                {"label": str(_("بازو منقبض")), "data": aligned("arm_flexed_cm")},
            ],
        },
        "wrist-forearm": {
            "labels": circ_labels,
            "datasets": [
                {"label": str(_("دور مچ دست")), "data": aligned("wrist_cm")},
                {"label": str(_("دور ساعد")), "data": aligned("forearm_cm")},
            ],
        },
        "thigh": {
            "labels": circ_labels,
            "datasets": [
                {"label": str(_("دور ران")), "data": aligned("thigh_cm")},
                {"label": str(_("دور ران (چپ)")), "data": aligned("thigh_left_cm")},
            ],
        },
        "calf": {
            "labels": circ_labels,
            "datasets": [
                {"label": str(_("دور ساق پا")), "data": aligned("calf_cm")},
                {"label": str(_("دور ساق پا (چپ)")), "data": aligned("calf_left_cm")},
            ],
        },
    }

    # --- History table (latest first) --------------------------------------
    history_rows = []
    for record in reversed(circ_records):
        weight = record.weight_kg
        height = record.height_cm
        bmi = calculate_bmi(weight, height)
        paired = find_caliper_for_datetime(user, measurement_datetime(record), caliper_records=cal_records)
        fat = body_fat_from_measurements(
            caliper=paired,
            circumference=record,
            height_cm=height,
            age=age_at_measurement(user, record),
            gender=gender,
            formula=selected_body_fat_formula,
            weight_kg=weight,
        )
        lean = calculate_lean_mass(weight, fat) if fat is not None else None
        history_rows.append(
            {
                "date": jdate(record),
                "bmi": bmi if bmi is not None else "—",
                "fat_percent": round(float(fat), 1) if fat is not None else "—",
                "lean_mass": lean if lean is not None else "—",
            }
        )

    # --- Gauges ------------------------------------------------------------
    bmi_now = calculate_bmi(user.weight_kg, user.height_cm)
    latest_circ = circ_records_all[-1] if circ_records_all else None
    whr_now = whr_from_circumference(latest_circ) if latest_circ else None

    def clamp_pct(value: float) -> int:
        return int(max(2, min(98, value)))

    gauges = {
        "bmi": {
            "value": bmi_now,
            "category": bmi_category_label(bmi_now) if bmi_now is not None else None,
            "pct": clamp_pct((bmi_now - 15) / 25 * 100) if bmi_now is not None else 0,
            "has_value": bmi_now is not None,
        },
        "whr": {
            "value": round(whr_now, 2) if whr_now is not None else None,
            "status": calculate_whr_status(whr_now, gender) if whr_now is not None else None,
            "pct": clamp_pct((whr_now - 0.7) / 0.4 * 100) if whr_now is not None else 0,
            "has_value": whr_now is not None,
        },
    }

    # --- Headline body stat cards (latest values) --------------------------
    latest = circ_records_all[-1] if circ_records_all else None
    stat_cards = []

    def add_card(icon, label, value, unit):
        if value not in (None, ""):
            stat_cards.append({"icon": icon, "label": label, "value": value, "unit": unit})

    cm = str(_("سانتی‌متر"))
    add_card("weight", _("وزن"), (latest.weight_kg if latest and latest.weight_kg else user.weight_kg), str(_("کیلوگرم")))
    add_card("height", _("قد"), (latest.height_cm if latest and latest.height_cm else user.height_cm), cm)
    if latest:
        add_card("waist", _("دور کمر"), latest.waist_cm, cm)
        add_card("hip", _("دور باسن"), latest.hips_cm, cm)
        add_card("chest", _("دور سینه"), latest.chest_cm, cm)
        add_card("arm", _("دور بازو منقبض"), latest.arm_flexed_cm, cm)

    smart_goal = build_smart_goal_data(
        user,
        circ_records=circ_records_all,
        cal_records=cal_records_all,
        age=age,
        body_fat_formula=selected_body_fat_formula,
    )

    return {
        "analysis_trend_charts": trend_charts,
        "analysis_measurement_groups": measurement_groups,
        "analysis_history_rows": history_rows,
        "analysis_gauges": gauges,
        "analysis_stat_cards": stat_cards,
        "analysis_has_chart_data": any(chart["values"] for chart in trend_charts.values()),
        "analysis_smart_goal": smart_goal,
    }
