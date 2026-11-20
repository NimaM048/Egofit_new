from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation

import jdatetime
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.translation import gettext_lazy as _

from account.models import (
    BodyCircumferenceMeasurement,
    CaliperMeasurement,
    MeasurementMethod,
    MeasurementSource,
)
from account.utils import normalize_digits


class MeasurementService:
    """Validate and persist dated, attributed body-measurement observations."""

    MODEL_BY_KIND = {
        "circumference": BodyCircumferenceMeasurement,
        "caliper": CaliperMeasurement,
    }
    METADATA_FIELDS = {
        "date",
        "measured_at",
        "measurement_method",
        "protocol_version",
        "trial_number",
    }

    @classmethod
    def metric_fields(cls, kind: str) -> tuple[str, ...]:
        model = cls.MODEL_BY_KIND.get(kind)
        if model is None:
            raise ValidationError({"kind": _("نوع اندازه‌گیری معتبر نیست.")})
        return tuple(
            field.name
            for field in model._meta.get_fields()
            if isinstance(field, models.DecimalField)
        )

    @classmethod
    def serialize(cls, record: models.Model) -> dict:
        fields = cls.metric_fields("caliper" if isinstance(record, CaliperMeasurement) else "circumference")
        return {
            "id": record.pk,
            "measured_at": timezone.localtime(record.measured_at).isoformat(),
            "source": record.measurement_source,
            "method": record.measurement_method,
            "protocol_version": record.protocol_version,
            "trial_number": record.trial_number,
            "values": {name: str(getattr(record, name)) if getattr(record, name) is not None else None for name in fields},
        }

    @staticmethod
    def _parse_measured_at(payload: dict):
        raw = str(payload.get("measured_at") or payload.get("date") or "").strip()
        if not raw:
            return timezone.now()
        raw = normalize_digits(raw)
        date_time = parse_datetime(raw)
        if date_time is not None:
            moment = timezone.make_aware(date_time, timezone.get_current_timezone()) if timezone.is_naive(date_time) else date_time
            if moment > timezone.now() + timedelta(minutes=5):
                raise ValidationError({"measured_at": _("زمان اندازه‌گیری نمی‌تواند در آینده باشد.")})
            return moment
        date_value = parse_date(raw)
        if date_value is None:
            parts = raw.replace("-", "/").split("/")
            try:
                if len(parts) != 3 or not (1300 <= int(parts[0]) <= 1600):
                    raise ValueError
                date_value = jdatetime.date(*(int(part) for part in parts)).togregorian()
            except (TypeError, ValueError) as exc:
                raise ValidationError({"date": _("تاریخ باید میلادی به شکل YYYY-MM-DD یا شمسی به شکل YYYY/MM/DD باشد.")}) from exc
        moment = datetime.combine(date_value, time(12, 0))
        moment = timezone.make_aware(moment, timezone.get_current_timezone())
        if moment > timezone.now() + timedelta(minutes=5):
            raise ValidationError({"date": _("تاریخ اندازه‌گیری نمی‌تواند در آینده باشد.")})
        return moment

    @classmethod
    @transaction.atomic
    def create(cls, *, user, payload: dict, kind: str, source: str, actor=None):
        model = cls.MODEL_BY_KIND.get(kind)
        if model is None:
            raise ValidationError({"kind": _("نوع اندازه‌گیری معتبر نیست.")})
        if not isinstance(payload, dict):
            raise ValidationError({"body": _("بدنه درخواست باید یک شیء JSON باشد.")})

        metric_fields = cls.metric_fields(kind)
        allowed_fields = set(metric_fields) | cls.METADATA_FIELDS
        unknown = set(payload) - allowed_fields
        if unknown:
            raise ValidationError({"fields": _("فیلدهای پشتیبانی‌نشده: %(fields)s") % {"fields": ", ".join(sorted(unknown))}})

        values = {}
        for name in metric_fields:
            raw = payload.get(name)
            if raw in (None, ""):
                continue
            try:
                value = Decimal(str(normalize_digits(raw)).replace(",", ".").replace("٫", "."))
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise ValidationError({name: _("مقدار باید عددی باشد.")}) from exc
            if not value.is_finite() or value <= 0:
                raise ValidationError({name: _("مقدار اندازه‌گیری باید بزرگ‌تر از صفر باشد.")})
            values[name] = value
        if not values:
            raise ValidationError({"values": _("حداقل یک مقدار اندازه‌گیری وارد کنید.")})

        method = str(payload.get("measurement_method") or (
            MeasurementMethod.SKINFOLD_CALIPER if kind == "caliper" else MeasurementMethod.SELF_REPORTED
        ))
        if method not in MeasurementMethod.values:
            raise ValidationError({"measurement_method": _("روش اندازه‌گیری معتبر نیست.")})
        try:
            trial_number = int(normalize_digits(payload.get("trial_number") or 1))
        except (TypeError, ValueError) as exc:
            raise ValidationError({"trial_number": _("شماره تکرار باید عدد صحیح مثبت باشد.")}) from exc
        if not 1 <= trial_number <= 99:
            raise ValidationError({"trial_number": _("شماره تکرار باید بین ۱ تا ۹۹ باشد.")})
        protocol_version = str(payload.get("protocol_version") or "").strip()
        if len(protocol_version) > 40:
            raise ValidationError({"protocol_version": _("نسخه پروتکل حداکثر ۴۰ نویسه است.")})

        record = model(
            user=user,
            measured_at=cls._parse_measured_at(payload),
            measurement_source=source,
            measurement_method=method,
            protocol_version=protocol_version,
            trial_number=trial_number,
            measured_by=actor,
            **values,
        )
        record.full_clean()
        record.save()

        if kind == "circumference":
            latest = model.objects.filter(user=user).exclude(pk=record.pk).order_by("-measured_at", "-pk").first()
            if latest is None or record.measured_at >= latest.measured_at:
                update_fields = []
                for field_name in ("height_cm", "weight_kg"):
                    value = getattr(record, field_name)
                    if value is not None:
                        setattr(user, field_name, value)
                        update_fields.append(field_name)
                if update_fields:
                    user.save(update_fields=update_fields)
        return record

    @classmethod
    def history(cls, *, user, kind: str, limit: int = 100):
        model = cls.MODEL_BY_KIND.get(kind)
        if model is None:
            raise ValidationError({"kind": _("نوع اندازه‌گیری معتبر نیست.")})
        return list(model.objects.filter(user=user).order_by("-measured_at", "-pk")[:limit])
