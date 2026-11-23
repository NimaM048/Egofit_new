"""Calendar rules and date windows for athlete analysis."""

from __future__ import annotations

from typing import Any

from account.services.profile_dashboard_service import ProfileDashboardService


def validate_jalali_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        import jdatetime

        parts = [int(part) for part in value.strip().split("/")]
        if len(parts) != 3:
            return None
        jdatetime.date(parts[0], parts[1], parts[2])
        return value.strip()
    except (ValueError, TypeError):
        return None


def jalali_from_datetime(dt) -> str:
    import jdatetime
    from django.utils import timezone

    local = timezone.localtime(dt)
    return jdatetime.datetime.fromgregorian(datetime=local).strftime("%Y/%m/%d")


def parse_jalali_date_obj(value: str):
    import jdatetime

    parts = [int(part) for part in value.split("/")]
    return jdatetime.date(parts[0], parts[1], parts[2])


def default_analysis_date_range(user) -> tuple[str, str]:
    import jdatetime
    from datetime import timedelta

    measurement_dates = []
    for record in user.circumference_records.all()[:100]:
        measurement_dates.append(parse_jalali_date_obj(record_jalali_date(record)))
    for record in user.caliper_records.all()[:100]:
        measurement_dates.append(parse_jalali_date_obj(record_jalali_date(record)))

    if measurement_dates:
        return min(measurement_dates).strftime("%Y/%m/%d"), max(measurement_dates).strftime("%Y/%m/%d")

    today = jdatetime.date.today()
    start = jdatetime.date.fromgregorian(date=today.togregorian() - timedelta(days=90))
    return start.strftime("%Y/%m/%d"), today.strftime("%Y/%m/%d")


def analysis_date_options(user) -> list[str]:
    dates = set()
    for record in user.circumference_records.all()[:120]:
        value = validate_jalali_date(record_jalali_date(record))
        if value:
            dates.add(value)
    for record in user.caliper_records.all()[:120]:
        value = validate_jalali_date(record_jalali_date(record))
        if value:
            dates.add(value)

    if not dates:
        start, end = default_analysis_date_range(user)
        dates.update((start, end))
    return sorted(dates, key=parse_jalali_date_obj, reverse=True)


def resolve_analysis_date_range(
    user,
    start: str | None = None,
    end: str | None = None,
) -> tuple[str, str, Any, Any]:
    default_start, default_end = default_analysis_date_range(user)
    start_date = validate_jalali_date(start) or default_start
    end_date = validate_jalali_date(end) or default_end
    start_j = parse_jalali_date_obj(start_date)
    end_j = parse_jalali_date_obj(end_date)
    if start_j > end_j:
        start_date, end_date = end_date, start_date
        start_j, end_j = end_j, start_j
    return start_date, end_date, start_j, end_j


def resolve_age(user) -> int | None:
    age = ProfileDashboardService._parse_birth_date_jalali(user.birth_date_jalali)
    if age is not None:
        return age
    return user.age


def age_at_measurement(user, record=None) -> int | None:
    """Return age on a measurement date when a birth date is available."""
    if record is None or not getattr(user, "birth_date_jalali", None):
        return resolve_age(user)
    try:
        import jdatetime

        birth_parts = [int(part) for part in user.birth_date_jalali.strip().replace("-", "/").split("/")]
        if len(birth_parts) != 3:
            return resolve_age(user)
        birth_date = jdatetime.date(*birth_parts)
        measured_date = parse_jalali_date_obj(record_jalali_date(record))
        age = measured_date.year - birth_date.year - (
            (measured_date.month, measured_date.day) < (birth_date.month, birth_date.day)
        )
        return age if age >= 0 else None
    except (TypeError, ValueError, AttributeError):
        return resolve_age(user)


def measurement_datetime(record):
    return getattr(record, "measured_at", None) or record.recorded_at


def record_jalali_date(record) -> str:
    if getattr(record, "measured_at_jalali", None):
        return record.measured_at_jalali
    return jalali_from_datetime(measurement_datetime(record))


def jalali_from_date(value) -> str:
    import jdatetime

    return jdatetime.date.fromgregorian(date=value).strftime("%Y/%m/%d")
