"""Analysis payloads and coach access to athlete measurements."""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone

from account.admin_forms import AdminCaliperForm, AdminCircumferenceForm, AdminHealthRecordForm
from account.models import (
    BodyCircumferenceMeasurement,
    CaliperMeasurement,
    User,
    UserHealthRecord,
    WorkoutPerformanceRecord,
)
from api.decorators import api_auth_required, api_endpoint, api_methods
from api.limits import rate_limit
from api.responses import error, ok
from api.utils import json_value, request_data
from api.v1.permissions import api_admin_required, api_coach_required

from .support import admin_portal_service, _audit, _coach_athlete
from .users import _serialize_user


def _parse_jalali_range(raw_start, raw_end):
    """Parse optional Jalali ``YYYY/MM/DD`` bounds into Gregorian dates.

    Coaches and administrators asked to narrow an athlete's history to a window
    such as ``1404/10/12`` to ``1405/8/15``. Measurement rows are filtered by
    their Gregorian ``measured_at`` timestamp. Invalid or missing values
    are ignored rather than raising so a stray query string never breaks the
    screen.
    """
    import jdatetime

    def _one(value):
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        # Mobile numeric keypads cannot always emit "/", so an 8-digit value
        # such as "14041012" is treated as "1404/10/12".
        digits = "".join(ch for ch in text if ch.isdigit())
        if "/" not in text and len(digits) == 8:
            text = f"{digits[:4]}/{digits[4:6]}/{digits[6:]}"
        try:
            parts = [int(part) for part in text.split("/")]
            if len(parts) != 3:
                return None
            return jdatetime.date(parts[0], parts[1], parts[2]).togregorian()
        except (ValueError, TypeError):
            return None

    start = _one(raw_start)
    end = _one(raw_end)
    if start and end and start > end:
        start, end = end, start
    return start, end


def _local_window(start, end):
    """Turn inclusive local calendar dates into an aware half-open datetime range.

    ``measured_at`` is a timezone-aware datetime. Filtering with ``__date``
    relies on the database's timezone tables (``CONVERT_TZ``), which are not
    guaranteed to be loaded on MySQL, so a local-midnight datetime window is
    used instead. The end bound is exclusive (start of the following day).
    """
    from datetime import datetime, time, timedelta

    lower = None
    upper = None
    if start:
        lower = timezone.make_aware(datetime.combine(start, time.min), timezone.get_current_timezone())
    if end:
        exclusive = end + timedelta(days=1)
        upper = timezone.make_aware(datetime.combine(exclusive, time.min), timezone.get_current_timezone())
    return lower, upper


def _filter_measurement_queryset(queryset, *, start, end):
    """Restrict a measurement queryset to the requested Gregorian window."""
    lower, upper = _local_window(start, end)
    if lower:
        queryset = queryset.filter(measured_at__gte=lower)
    if upper:
        queryset = queryset.filter(measured_at__lt=upper)
    return queryset


def _analysis_payload(user, *, start=None, end=None, coach_scope=False):
    range_start, range_end = _parse_jalali_range(start, end)
    hidden_measurement_fields = {"id", "user", "legacy_id", "recorded_at", "measured_at_jalali"}
    circumference_fields = [field.name for field in BodyCircumferenceMeasurement._meta.fields if field.name not in hidden_measurement_fields]
    caliper_fields = [field.name for field in CaliperMeasurement._meta.fields if field.name not in hidden_measurement_fields]
    circumference_queryset = _filter_measurement_queryset(
        BodyCircumferenceMeasurement.objects.filter(user=user), start=range_start, end=range_end
    )
    caliper_queryset = _filter_measurement_queryset(
        CaliperMeasurement.objects.filter(user=user), start=range_start, end=range_end
    )
    health_queryset = UserHealthRecord.objects.filter(user=user)
    performance_queryset = WorkoutPerformanceRecord.objects.filter(user=user)
    if range_start:
        health_queryset = health_queryset.filter(recorded_at__gte=range_start)
        performance_queryset = performance_queryset.filter(recorded_on__gte=range_start)
    if range_end:
        health_queryset = health_queryset.filter(recorded_at__lte=range_end)
        performance_queryset = performance_queryset.filter(recorded_on__lte=range_end)
    if coach_scope:
        health_queryset = health_queryset.filter(
            category__in=(UserHealthRecord.CategoryChoices.INJURY, UserHealthRecord.CategoryChoices.ABNORMALITY),
            status=UserHealthRecord.Status.ACTIVE,
        ).filter(Q(training_restriction=True) | Q(body_region__gt=""))
    health_payload = []
    for item in health_queryset[:100]:
        if coach_scope:
            health_payload.append({
                "id": item.pk,
                "category": item.category,
                "body_region": item.body_region,
                "affected_side": item.affected_side,
                "severity": item.severity,
                "onset_date": json_value(item.onset_date),
                "status": item.status,
                "resolved_date": json_value(item.resolved_date),
                "training_restriction": item.training_restriction,
                "training_restriction_notes": item.training_restriction_notes,
            })
        else:
            health_payload.append({
                "id": item.pk,
                "category": item.category,
                "title": item.title,
                "details": item.details,
                "recorded_at": json_value(item.recorded_at),
                "body_region": item.body_region,
                "affected_side": item.affected_side,
                "severity": item.severity,
                "onset_date": json_value(item.onset_date),
                "status": item.status,
                "resolved_date": json_value(item.resolved_date),
                "training_restriction": item.training_restriction,
                "training_restriction_notes": item.training_restriction_notes,
            })
    return {
        "profile": {
            "weight_kg": json_value(user.weight_kg),
            "height_cm": json_value(user.height_cm),
            "birth_date_jalali": user.birth_date_jalali,
            "gender": user.gender,
            "activity_level": user.activity_level,
        },
        "range": {
            "start": start if range_start else None,
            "end": end if range_end else None,
        },
        "circumference_measurements": [
            {"id": row.pk, **{key: json_value(getattr(row, key)) for key in circumference_fields}}
            for row in circumference_queryset[:100]
        ],
        "caliper_measurements": [
            {"id": row.pk, **{key: json_value(getattr(row, key)) for key in caliper_fields}}
            for row in caliper_queryset[:100]
        ],
        "health_records": health_payload,
        "workout_performance": list(
            performance_queryset
            .select_related("exercise", "program")
            .values(
                "id",
                "exercise_id",
                "exercise__name",
                "exercise_name_snapshot",
                "program_id",
                "program__title",
                "repetitions",
                "mode",
                "set_number",
                "value",
                "recorded_on",
                "updated_at",
            )[:200]
        ),
    }


@api_endpoint
@api_methods("GET")
@api_auth_required
def analysis_me(request):
    return ok(_analysis_payload(request.user, start=request.GET.get("start"), end=request.GET.get("end")))


@api_endpoint
@api_methods("GET")
@api_auth_required
@api_admin_required
def admin_user_analysis(request, user_id):
    user = get_object_or_404(User, pk=user_id)
    _audit(request.user, "user.analysis.read", object_type="user", object_id=user.pk)
    return ok({
        "user": _serialize_user(user),
        **_analysis_payload(user, start=request.GET.get("start"), end=request.GET.get("end")),
    })


@api_endpoint
@api_methods("GET")
@api_auth_required
@api_coach_required
def coach_athlete_analysis(request, athlete_id):
    athlete = _coach_athlete(request, athlete_id)
    _audit(request.user, "athlete.analysis.read", object_type="user", object_id=athlete.pk)
    return ok({
        "user": _serialize_user(athlete),
        **_analysis_payload(athlete, start=request.GET.get("start"), end=request.GET.get("end"), coach_scope=True),
    })


def _measurement_payload(data) -> dict:
    """Accept numeric measurements and normalize an optional Jalali date."""
    if not isinstance(data, dict):
        raise ValidationError({"body": "Expected a JSON object."})
    import jdatetime

    cleaned = {key: value for key, value in data.items() if key not in {"date", "measured_at"}}
    jalali = str(data.get("date") or data.get("measured_at_jalali") or "").strip()
    if jalali:
        gregorian = _parse_jalali_range(jalali, None)[0]
        if gregorian is None:
            raise ValidationError({"date": "تاریخ باید به شکل 1404/10/12 باشد."})
        cleaned["measured_at_jalali"] = jdatetime.date.fromgregorian(date=gregorian).strftime("%Y/%m/%d")
    return cleaned


def _backdate_record(record, gregorian_date):
    """Compatibility helper for legacy measurement rows."""
    if gregorian_date is None:
        return
    from datetime import datetime, time

    moment = timezone.make_aware(
        datetime.combine(gregorian_date, time(12, 0)), timezone.get_current_timezone()
    )
    type(record).objects.filter(pk=record.pk).update(measured_at=moment)
    record.measured_at = moment


@api_endpoint
@api_methods("POST")
@api_auth_required
@api_coach_required
@rate_limit(name="coach-analysis-write", limit=60, window=3600)
def coach_athlete_measurements(request, athlete_id, kind):
    """Let a coach record circumference, caliper or health data (requirement 3c)."""
    athlete = _coach_athlete(request, athlete_id)
    payload = request_data(request)
    if kind == "health":
        if not isinstance(payload, dict):
            raise ValidationError({"body": "Expected a JSON object."})
        data = payload
    else:
        data = _measurement_payload(payload)

    if kind == "circumference":
        instance = admin_portal_service.get_or_create_circumference(athlete)
        form = AdminCircumferenceForm(data, instance=instance)
        if not form.is_valid():
            raise ValidationError(form.errors)
        from account.models import MeasurementSource

        record = admin_portal_service.save_circumference(
            user=athlete, form=form, actor=request.user, source=MeasurementSource.COACH
        )
        action = "measurement.circumference.create"
    elif kind == "caliper":
        instance = admin_portal_service.get_or_create_caliper(athlete)
        form = AdminCaliperForm(data, instance=instance)
        if not form.is_valid():
            raise ValidationError(form.errors)
        from account.models import MeasurementSource

        record = admin_portal_service.save_caliper(
            user=athlete, form=form, actor=request.user, source=MeasurementSource.COACH
        )
        action = "measurement.caliper.create"
    elif kind == "health":
        if "recorded_at" not in data:
            # The health form stores a Gregorian date; translate the Jalali
            # ``date`` the mobile editor sends, defaulting to "today".
            jalali = str(data.get("date") or "").strip()
            gregorian = _parse_jalali_range(jalali, None)[0] if jalali else timezone.localdate()
            if gregorian is None:
                raise ValidationError({"date": "تاریخ باید به شکل 1404/10/12 باشد."})
            data = {**{k: v for k, v in data.items() if k != "date"}, "recorded_at": gregorian.isoformat()}
        else:
            data = {key: value for key, value in data.items() if key != "date"}
        category = data.get("category")
        safe_categories = {UserHealthRecord.CategoryChoices.INJURY, UserHealthRecord.CategoryChoices.ABNORMALITY}
        if category not in safe_categories:
            raise ValidationError({"category": "Coaches may record training-relevant injury or abnormality entries only."})
        allowed_health_fields = {
            "category", "body_region", "affected_side", "severity", "onset_date", "status",
            "resolved_date", "training_restriction", "training_restriction_notes", "recorded_at",
        }
        unsupported = set(data) - allowed_health_fields
        if unsupported:
            raise ValidationError({"fields": "Unsupported health fields: " + ", ".join(sorted(unsupported))})
        data["title"] = dict(UserHealthRecord.CategoryChoices.choices).get(category, "سابقه تمرینی")
        form = AdminHealthRecordForm(data)
        if not form.is_valid():
            raise ValidationError(form.errors)
        record = admin_portal_service.save_health_record(user=athlete, form=form)
        action = "health_record.create"
    else:
        return error("not_found", "Unknown measurement kind.", status=404)

    _audit(request.user, action, object_type="user", object_id=athlete.pk)
    measurement = {"id": record.pk, "kind": kind}
    if kind in {"circumference", "caliper"}:
        measurement.update(
            {
                "measured_at": json_value(record.measured_at),
                "source": record.measurement_source,
                "method": record.measurement_method,
                "protocol_version": record.protocol_version,
                "trial_number": record.trial_number,
            }
        )
    return ok({"measurement": measurement}, status=201)
