from __future__ import annotations

import math
from typing import Any

from django.utils.translation import gettext_lazy as _

from account.domain.body_composition.calculator import (
    CaliperSkinfolds,
    CircumferenceMeasures,
    activity_factor_for_level,
    bmi_category_label,
    build_body_composition_snapshot,
    calculate_bmi,
    calculate_bmr_mifflin_st_jeor,
    calculate_lean_mass,
    calculate_tdee,
    calculate_whr,
    calculate_whr_status,
    calculate_whtr,
    resolve_body_fat_result,
)
from account.services.profile_dashboard_service import ProfileDashboardService


class ProfileAnalysisService:
    """Build profile body-composition analysis payloads.

    This service owns the analysis presentation orchestration while the
    Django-independent formulas remain in account.domain.body_composition.
    Keep the returned context keys stable because templates and admin views
    consume the same payload.
    """

    @staticmethod
    def _segmented_bar(marker: int, *, zones: list[dict] | None = None) -> dict:
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
    @staticmethod
    def _build_chart(points: list[int], *, width: int = 320, height: int = 160, top_pad: int = 18, bottom_pad: int = 34):
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

    ANALYSIS_METRIC_OPTIONS = [
        {"key": "body_fat", "label": _("درصد چربی بدن")},
        {"key": "lean_mass", "label": _("توده بدون چربی")},
        {"key": "whr", "label": "WHR"},
        {"key": "whtr", "label": "WHtR"},
        {"key": "bmi", "label": "BMI"},
        {"key": "bmr", "label": "BMR"},
    ]

    BODY_FAT_FORMULA_OPTIONS = [
        {"key": "auto", "label": _("خودکار (بهترین فرمول موجود)")},
        {"key": "jp7", "label": "Jackson-Pollock 7-site"},
        {"key": "jp4", "label": "Jackson-Pollock 4-site"},
        {"key": "jp3", "label": "Jackson-Pollock 3-site"},
        {"key": "circumference", "label": _("درصد چربی (دورسنجی — Navy)")},
    ]

    @classmethod
    def _analysis_metric_label(cls, metric: str) -> str:
        for option in cls.ANALYSIS_METRIC_OPTIONS:
            if option["key"] == metric:
                return option["label"]
        return _("درصد چربی بدن")

    @staticmethod
    def _validate_jalali_date(value: str | None) -> str | None:
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

    @staticmethod
    def _jalali_from_datetime(dt) -> str:
        import jdatetime
        from django.utils import timezone

        local = timezone.localtime(dt)
        return jdatetime.datetime.fromgregorian(datetime=local).strftime("%Y/%m/%d")

    @classmethod
    def _parse_jalali_date_obj(cls, value: str):
        import jdatetime

        parts = [int(part) for part in value.split("/")]
        return jdatetime.date(parts[0], parts[1], parts[2])

    @classmethod
    def _default_analysis_date_range(cls, user) -> tuple[str, str]:
        import jdatetime
        from datetime import timedelta

        measurement_dates = []
        for record in user.circumference_records.all()[:100]:
            measurement_dates.append(cls._parse_jalali_date_obj(cls._record_jalali_date(record)))
        for record in user.caliper_records.all()[:100]:
            measurement_dates.append(cls._parse_jalali_date_obj(cls._record_jalali_date(record)))

        if measurement_dates:
            return min(measurement_dates).strftime("%Y/%m/%d"), max(measurement_dates).strftime("%Y/%m/%d")

        today = jdatetime.date.today()
        start = jdatetime.date.fromgregorian(date=today.togregorian() - timedelta(days=90))
        return start.strftime("%Y/%m/%d"), today.strftime("%Y/%m/%d")

    @classmethod
    def _analysis_date_options(cls, user) -> list[str]:
        dates = set()
        for record in user.circumference_records.all()[:120]:
            value = cls._validate_jalali_date(cls._record_jalali_date(record))
            if value:
                dates.add(value)
        for record in user.caliper_records.all()[:120]:
            value = cls._validate_jalali_date(cls._record_jalali_date(record))
            if value:
                dates.add(value)

        if not dates:
            start, end = cls._default_analysis_date_range(user)
            dates.update((start, end))
        return sorted(dates, key=cls._parse_jalali_date_obj, reverse=True)

    @classmethod
    def _resolve_analysis_date_range(
        cls,
        user,
        start: str | None = None,
        end: str | None = None,
    ) -> tuple[str, str, Any, Any]:
        default_start, default_end = cls._default_analysis_date_range(user)
        start_date = cls._validate_jalali_date(start) or default_start
        end_date = cls._validate_jalali_date(end) or default_end
        start_j = cls._parse_jalali_date_obj(start_date)
        end_j = cls._parse_jalali_date_obj(end_date)
        if start_j > end_j:
            start_date, end_date = end_date, start_date
            start_j, end_j = end_j, start_j
        return start_date, end_date, start_j, end_j

    @staticmethod
    def _resolve_age(user) -> int | None:
        age = ProfileDashboardService._parse_birth_date_jalali(user.birth_date_jalali)
        if age is not None:
            return age
        return user.age

    @classmethod
    def _age_at_measurement(cls, user, record=None) -> int | None:
        """Return age on a measurement date when a birth date is available."""
        if record is None or not getattr(user, "birth_date_jalali", None):
            return cls._resolve_age(user)
        try:
            import jdatetime

            birth_parts = [int(part) for part in user.birth_date_jalali.strip().replace("-", "/").split("/")]
            if len(birth_parts) != 3:
                return cls._resolve_age(user)
            birth_date = jdatetime.date(*birth_parts)
            measured_date = cls._parse_jalali_date_obj(cls._record_jalali_date(record))
            age = measured_date.year - birth_date.year - (
                (measured_date.month, measured_date.day) < (birth_date.month, birth_date.day)
            )
            return age if age >= 0 else None
        except (TypeError, ValueError, AttributeError):
            return cls._resolve_age(user)

    @classmethod
    def _build_measurement_display(cls, circumference, caliper) -> dict:
        circumference_rows = []
        caliper_rows = []

        if circumference:
            for field_name, label in cls.CIRCUMFERENCE_FIELD_LABELS:
                value = getattr(circumference, field_name, None)
                if value not in (None, ""):
                    circumference_rows.append({"label": label, "value": f"{value} {_('سانتی‌متر')}"})

        if caliper:
            for field_name, label in cls.CALIPER_FIELD_LABELS:
                value = getattr(caliper, field_name, None)
                if value not in (None, ""):
                    caliper_rows.append({"label": label, "value": f"{value} {_('میلی‌متر')}"})

        return {
            "circumference_rows": circumference_rows,
            "caliper_rows": caliper_rows,
            "has_measurements": bool(circumference_rows or caliper_rows),
        }

    @classmethod
    def _find_caliper_for_datetime(cls, user, dt, caliper_records=None):
        import jdatetime
        from django.utils import timezone

        target_local = timezone.localtime(dt)
        target_j = jdatetime.datetime.fromgregorian(datetime=target_local).date()
        records = caliper_records if caliper_records is not None else user.caliper_records.all()
        for record in records:
            record_local = timezone.localtime(cls._measurement_datetime(record))
            record_j = jdatetime.datetime.fromgregorian(datetime=record_local).date()
            if record_j == target_j:
                return record
        return None

    @classmethod
    def _find_circumference_for_datetime(cls, user, dt, circumference_records=None):
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
            record_local = timezone.localtime(cls._measurement_datetime(record))
            record_j = jdatetime.datetime.fromgregorian(datetime=record_local).date()
            if record_j == target_j:
                return record
        return None

    @staticmethod
    def _measurement_datetime(record):
        return getattr(record, "measured_at", None) or record.recorded_at

    @staticmethod
    def _body_fat_from_caliper(
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

    @staticmethod
    def _body_fat_from_measurements(
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

    @staticmethod
    def _whr_from_circumference(circumference) -> float | None:
        measures = CircumferenceMeasures.from_model(circumference)
        if measures is None:
            return None
        return calculate_whr(measures.waist_cm, measures.hips_cm)

    def _whtr_from_circumference(self, user, circumference) -> float | None:
        measures = CircumferenceMeasures.from_model(circumference)
        if measures is None:
            return None
        return calculate_whtr(measures.waist_cm, user.height_cm)

    def _bmr_value(self, user, age) -> int | None:
        from account.domain.body_composition.calculator import calculate_bmr_mifflin_st_jeor

        return calculate_bmr_mifflin_st_jeor(
            weight_kg=user.weight_kg,
            height_cm=user.height_cm,
            age=age,
            gender=user.gender,
        )

    def _metric_value_from_records(
        self,
        user,
        metric: str,
        circumference,
        caliper,
        *,
        body_fat_formula: str | None = None,
    ):
        age = self._age_at_measurement(user, circumference or caliper)
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

    def _metric_series_points(
        self,
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
        age = self._resolve_age(user)
        gender = user.gender
        selected_body_fat_formula = self._resolve_body_fat_formula(body_fat_formula)
        circ_records = circumference_records if circumference_records is not None else user.circumference_records.all()
        cal_records = caliper_records if caliper_records is not None else user.caliper_records.all()

        if metric == "body_fat":
            if selected_body_fat_formula == "circumference":
                for record in circ_records:
                    j_date = self._parse_jalali_date_obj(self._record_jalali_date(record))
                    if start_j <= j_date <= end_j:
                        paired_caliper = self._find_caliper_for_datetime(user, self._measurement_datetime(record), caliper_records=cal_records)
                        weight = record.weight_kg if record.weight_kg is not None else user.weight_kg
                        height = record.height_cm if record.height_cm is not None else user.height_cm
                        value = self._body_fat_from_measurements(
                            caliper=paired_caliper,
                            circumference=record,
                            height_cm=height,
                            age=self._age_at_measurement(user, record),
                            gender=gender,
                            formula=selected_body_fat_formula,
                            weight_kg=weight,
                        )
                        if value is not None:
                            series.append((j_date, value))
            else:
                for record in cal_records:
                    j_date = self._parse_jalali_date_obj(self._record_jalali_date(record))
                    if start_j <= j_date <= end_j:
                        paired_circumference = self._find_circumference_for_datetime(
                            user,
                            self._measurement_datetime(record),
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
                        value = self._body_fat_from_caliper(
                            record,
                            age=self._age_at_measurement(user, record),
                            gender=gender,
                            formula=selected_body_fat_formula,
                            weight_kg=weight,
                            height_cm=height,
                        )
                        if value is not None:
                            series.append((j_date, value))
                for record in circ_records:
                    measurement_age = self._age_at_measurement(user, record)
                    if measurement_age is None or not 7 <= measurement_age < 18:
                        continue
                    if self._find_caliper_for_datetime(
                        user,
                        self._measurement_datetime(record),
                        caliper_records=cal_records,
                    ):
                        continue
                    j_date = self._parse_jalali_date_obj(self._record_jalali_date(record))
                    if start_j <= j_date <= end_j:
                        weight = record.weight_kg if record.weight_kg is not None else user.weight_kg
                        height = record.height_cm if record.height_cm is not None else user.height_cm
                        value = self._body_fat_from_measurements(
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
                j_date = self._parse_jalali_date_obj(self._record_jalali_date(record))
                if start_j <= j_date <= end_j:
                    value = (
                        self._whr_from_circumference(record)
                        if metric == "whr"
                        else self._whtr_from_circumference(user, record)
                    )
                    if value is not None:
                        series.append((j_date, value))
        elif metric == "lean_mass":
            if selected_body_fat_formula == "circumference":
                for record in circ_records:
                    j_date = self._parse_jalali_date_obj(self._record_jalali_date(record))
                    if start_j <= j_date <= end_j:
                        paired_caliper = self._find_caliper_for_datetime(user, self._measurement_datetime(record), caliper_records=cal_records)
                        weight = record.weight_kg if record.weight_kg is not None else user.weight_kg
                        height = record.height_cm if record.height_cm is not None else user.height_cm
                        body_fat = self._body_fat_from_measurements(
                            caliper=paired_caliper,
                            circumference=record,
                            height_cm=height,
                            age=self._age_at_measurement(user, record),
                            gender=gender,
                            formula=selected_body_fat_formula,
                            weight_kg=weight,
                        )
                        value = calculate_lean_mass(weight, body_fat)
                        if value is not None:
                            series.append((j_date, value))
            else:
                for record in cal_records:
                    j_date = self._parse_jalali_date_obj(self._record_jalali_date(record))
                    if start_j <= j_date <= end_j:
                        paired_circumference = self._find_circumference_for_datetime(
                            user,
                            self._measurement_datetime(record),
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
                        body_fat = self._body_fat_from_caliper(
                            record,
                            age=self._age_at_measurement(user, record),
                            gender=gender,
                            formula=selected_body_fat_formula,
                            weight_kg=weight,
                            height_cm=height,
                        )
                        value = calculate_lean_mass(weight, body_fat)
                        if value is not None:
                            series.append((j_date, value))
                for record in circ_records:
                    measurement_age = self._age_at_measurement(user, record)
                    if measurement_age is None or not 7 <= measurement_age < 18:
                        continue
                    if self._find_caliper_for_datetime(
                        user,
                        self._measurement_datetime(record),
                        caliper_records=cal_records,
                    ):
                        continue
                    j_date = self._parse_jalali_date_obj(self._record_jalali_date(record))
                    if start_j <= j_date <= end_j:
                        weight = record.weight_kg if record.weight_kg is not None else user.weight_kg
                        height = record.height_cm if record.height_cm is not None else user.height_cm
                        body_fat = self._body_fat_from_measurements(
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
                j_date = self._parse_jalali_date_obj(self._record_jalali_date(record))
                if start_j <= j_date <= end_j:
                    weight = record.weight_kg
                    height = record.height_cm
                    bmi = calculate_bmi(weight, height)
                    if bmi is not None:
                        series.append((j_date, bmi))
        elif metric == "bmr":
            bmr = self._bmr_value(user, age)
            if bmr is not None:
                for record in circ_records:
                    j_date = self._parse_jalali_date_obj(self._record_jalali_date(record))
                    if start_j <= j_date <= end_j:
                        series.append((j_date, bmr))
        else:
            for record in circ_records:
                j_date = self._parse_jalali_date_obj(self._record_jalali_date(record))
                if start_j <= j_date <= end_j:
                    paired_caliper = self._find_caliper_for_datetime(user, self._measurement_datetime(record), caliper_records=cal_records)
                    value = self._metric_value_from_records(
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

    def _collect_metric_series(
        self,
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
            for _, value in self._metric_series_points(
                user,
                metric,
                start_j,
                end_j,
                body_fat_formula=body_fat_formula,
                circumference_records=circumference_records,
                caliper_records=caliper_records,
            )
        ]

    @staticmethod
    def _metric_round(metric: str, value: float):
        if metric in ("whr", "whtr"):
            return round(float(value), 2)
        if metric == "bmr":
            return int(round(float(value)))
        if metric in ("body_fat", "lean_mass", "bmi"):
            return round(float(value), 1)
        return round(float(value), 1)

    @staticmethod
    def _metric_unit(metric: str) -> str:
        if metric == "body_fat":
            return "%"
        if metric == "lean_mass":
            return str(_("کیلوگرم"))
        if metric == "bmr":
            return "kcal"
        return ""

    @staticmethod
    def _status_tone(text) -> str:
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

    @staticmethod
    def _tile_number(value, *, decimals: int = 1):
        if value in (None, ""):
            return "—"
        if decimals == 0:
            return int(round(float(value)))
        return round(float(value), decimals)

    def get_analysis_metric_series(
        self,
        user: Any,
        *,
        body_fat_formula: str | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> dict[str, Any]:
        """Return normalized trend-series data for the analysis charts."""
        """Per-metric labelled time series for the client-side metric dropdown chart."""
        start_date, end_date, start_j, end_j = self._resolve_analysis_date_range(user, start, end)
        selected_body_fat_formula = self._resolve_body_fat_formula(body_fat_formula)
        series: dict = {}
        for option in self.ANALYSIS_METRIC_OPTIONS:
            key = option["key"]
            points = self._metric_series_points(
                user,
                key,
                start_j,
                end_j,
                body_fat_formula=selected_body_fat_formula,
            )
            series[key] = {
                "labels": [point[0].strftime("%Y/%m/%d") for point in points],
                "values": [self._metric_round(key, point[1]) for point in points],
                "label": str(option["label"]),
                "unit": self._metric_unit(key),
            }
        return series

    @staticmethod
    def _scale_series_for_chart(metric: str, values: list[float]) -> list[int]:
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

    def _format_metric_display(self, metric: str, value) -> str:
        if value in (None, ""):
            return _("ثبت نشده")
        if metric == "body_fat":
            return f"{value}%"
        if metric == "lean_mass":
            return f"{value} {_('کیلوگرم')}"
        if metric == "bmr":
            return str(int(value))
        return str(value)

    @classmethod
    def _record_jalali_date(cls, record) -> str:
        if getattr(record, "measured_at_jalali", None):
            return record.measured_at_jalali
        return cls._jalali_from_datetime(cls._measurement_datetime(record))

    @classmethod
    def _resolve_body_fat_formula(cls, value: str | None) -> str:
        valid = {option["key"] for option in cls.BODY_FAT_FORMULA_OPTIONS}
        formula = (value or "auto").strip().lower()
        return formula if formula in valid else "auto"

    @classmethod
    def _body_fat_formula_label(cls, formula_key: str) -> str:
        for option in cls.BODY_FAT_FORMULA_OPTIONS:
            if option["key"] == formula_key:
                return option["label"]
        return formula_key

    def get_analysis_context(
        self,
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
        valid_metrics = {option["key"] for option in self.ANALYSIS_METRIC_OPTIONS}
        selected_metric = metric if metric in valid_metrics else "body_fat"
        if use_full_chart_range and not start and not end:
            chart_start_date, chart_end_date = self._default_analysis_date_range(user)
            start_j = self._parse_jalali_date_obj(chart_start_date)
            end_j = self._parse_jalali_date_obj(chart_end_date)
        else:
            chart_start_date, chart_end_date, start_j, end_j = self._resolve_analysis_date_range(
                user,
                start,
                end,
            )

        bmi = calculate_bmi(user.weight_kg, user.height_cm)
        bmi_label = bmi_category_label(bmi)
        age = self._resolve_age(user)
        has_data = any(
            value not in (None, "")
            for value in (user.weight_kg, user.height_cm, user.birth_date_jalali, user.blood_group, user.gender)
        ) or user.circumference_records.exists() or user.caliper_records.exists()

        circumference_records = list(user.circumference_records.all()[:120])
        caliper_records = list(user.caliper_records.all()[:120])
        circ_by_date: dict = {}
        for record in sorted(circumference_records, key=self._measurement_datetime, reverse=True):
            circ_by_date.setdefault(self._record_jalali_date(record), record)
        cal_by_date: dict = {}
        for record in sorted(caliper_records, key=self._measurement_datetime, reverse=True):
            cal_by_date.setdefault(self._record_jalali_date(record), record)

        circ_dates = sorted(circ_by_date.keys(), reverse=True)
        analysis_circ_dates = [{"value": value, "label": value} for value in circ_dates]
        selected_body_fat_formula = self._resolve_body_fat_formula(body_fat_formula)
        analysis_body_fat_formulas = list(self.BODY_FAT_FORMULA_OPTIONS)
        if age is not None and age < 18:
            selected_body_fat_formula = "auto"

        legacy_date = self._validate_jalali_date(date)
        selected_circ_date = self._validate_jalali_date(circ_date) or legacy_date
        if selected_circ_date not in circ_dates:
            selected_circ_date = circ_dates[0] if circ_dates else None

        caliper_dates = sorted(cal_by_date.keys(), reverse=True)
        selected_date = selected_circ_date or (caliper_dates[0] if caliper_dates else None)

        latest_circumference = circ_by_date.get(selected_circ_date) or (circumference_records[0] if circumference_records else None)
        paired_caliper = (
            self._find_caliper_for_datetime(user, self._measurement_datetime(latest_circumference), caliper_records=caliper_records)
            if latest_circumference
            else (caliper_records[0] if caliper_records else None)
        ) or (caliper_records[0] if caliper_records else None)

        skinfolds = CaliperSkinfolds.from_model(paired_caliper)
        measures = CircumferenceMeasures.from_model(latest_circumference)
        snapshot_weight = latest_circumference.weight_kg if latest_circumference and latest_circumference.weight_kg is not None else user.weight_kg
        snapshot_height = latest_circumference.height_cm if latest_circumference and latest_circumference.height_cm is not None else user.height_cm
        snapshot_age = self._age_at_measurement(user, latest_circumference or paired_caliper)
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
        measurements = self._build_measurement_display(latest_circumference, paired_caliper)

        raw_series = self._collect_metric_series(
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
            chart_input = self._scale_series_for_chart(selected_metric, raw_series)
            chart_points, chart_path, chart_area_path = self._build_chart(chart_input)
        else:
            chart_points, chart_path, chart_area_path = [], "", ""

        body_fat = composition.body_fat_percent
        current_value = self._metric_value_from_records(
            user,
            selected_metric,
            latest_circumference,
            paired_caliper,
            body_fat_formula=selected_body_fat_formula,
        )
        analysis_value = self._format_metric_display(selected_metric, current_value)
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

        filter_label = self._analysis_metric_label(selected_metric)
        if use_full_chart_range and chart_has_real_data:
            range_label = _("روند کامل اندازه‌گیری‌ها")
        else:
            range_label = _("از تاریخ %(start_date)s تا تاریخ %(end_date)s") % {
                "start_date": chart_start_date,
                "end_date": chart_end_date,
            }

        # --- Calculation result tiles (clean numeric squares; no somatotype) ----
        whr_value = composition.whr if composition.whr is not None else (
            self._whr_from_circumference(latest_circumference) if latest_circumference else None
        )
        analysis_result_tiles = [
            {
                "key": "body_fat",
                "label": _("درصد چربی بدن"),
                "value": self._tile_number(body_fat),
                "unit": "%",
                "tone": "neutral",
                "status": "",
            },
            {
                "key": "lean_mass",
                "label": _("توده بدون چربی"),
                "value": self._tile_number(composition.lean_mass_kg),
                "unit": "kg",
                "tone": "neutral",
                "status": "",
            },
            {
                "key": "whr",
                "label": "WHR",
                "value": self._tile_number(whr_value, decimals=2),
                "unit": "",
                "tone": self._status_tone(composition.whr_status),
                "status": composition.whr_status or "",
            },
            {
                "key": "whtr",
                "label": "WHtR",
                "value": self._tile_number(composition.whtr, decimals=2),
                "unit": "",
                "tone": self._status_tone(composition.whtr_status),
                "status": composition.whtr_status or "",
            },
            {
                "key": "bmi",
                "label": "BMI",
                "value": bmi if bmi is not None else "—",
                "unit": "",
                "tone": self._status_tone(bmi_label) if bmi is not None else "neutral",
                "status": bmi_label if bmi is not None else "",
            },
            {
                "key": "bmr",
                "label": "BMR",
                "value": self._tile_number(composition.bmr, decimals=0),
                "unit": "kcal",
                "tone": "neutral",
                "status": "",
            },
            {
                "key": "calories",
                "label": _("کالری روزانه"),
                "value": self._tile_number(composition.tdee, decimals=0),
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

        analysis_range_dates = self._analysis_date_options(user)
        for value in (chart_start_date, chart_end_date):
            if value not in analysis_range_dates:
                analysis_range_dates.append(value)
        analysis_range_dates.sort(key=self._parse_jalali_date_obj, reverse=True)

        return {
            "analysis_has_data": has_data,
            "analysis_selected_metric": selected_metric,
            "analysis_filter_label": filter_label,
            "analysis_metric_options": self.ANALYSIS_METRIC_OPTIONS,
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
                else self._body_fat_formula_label(selected_body_fat_formula)
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
                    "value": self._format_metric_display("body_fat", body_fat),
                    **self._segmented_bar(int(body_fat) if body_fat else 18),
                },
                {
                    "label": _("توده عضلانی بدون چربی"),
                    "value": self._format_metric_display(
                        "lean_mass",
                        composition.lean_mass_kg,
                    ),
                    **self._segmented_bar(58 if user.weight_kg else 16),
                },
                {
                    "label": "WHR",
                    "value": self._format_metric_display(
                        "whr",
                        self._whr_from_circumference(latest_circumference) if latest_circumference else None,
                    ),
                    **self._segmented_bar(
                        int((self._whr_from_circumference(latest_circumference) or 0.46) * 100)
                        if latest_circumference
                        else 14
                    ),
                },
                {
                    "label": _("وضعیت WHR"),
                    "value": composition.whr_status or _("ثبت نشده"),
                    **self._segmented_bar(int((composition.whr or 0.46) * 100)),
                },
                {
                    "label": _("وضعیت WHtR"),
                    "value": composition.whtr_status or _("ثبت نشده"),
                    **self._segmented_bar(int((composition.whtr or 0.46) * 100)),
                },
                {
                    "label": _("اندازه قاب بدن"),
                    "value": composition.body_frame_size or _("ثبت نشده"),
                    **self._segmented_bar(18),
                },
                {
                    "label": _("سوماتوتایپ (اندومورف/مزومورف/اکتومورف)"),
                    "value": (
                        f"{composition.endomorphy} / {composition.mesomorphy} / {composition.ectomorphy}"
                        if composition.endomorphy is not None
                        else _("ثبت نشده")
                    ),
                    **self._segmented_bar(20),
                },
                {
                    "label": "BMI",
                    "value": bmi if bmi is not None else _("ثبت نشده"),
                    **self._segmented_bar(round(min(bmi or 0, 40) * 2.2) if bmi is not None else 18),
                },
                {
                    "label": "BMR",
                    "value": bmr_display,
                    **self._segmented_bar(42 if bmi is not None else 18),
                },
                {
                    "label": _("کالری مورد نیاز روزانه"),
                    "value": daily_calories,
                    **self._segmented_bar(22 if bmi is not None else 14),
                },
                {
                    "label": _("پروتئین مورد نیاز روزانه"),
                    "value": daily_protein,
                    **self._segmented_bar(16 if user.weight_kg else 14),
                },
            ],
            "analysis_chart_points": chart_points,
            "analysis_chart_path": chart_path,
            "analysis_chart_area_path": chart_area_path,
            "analysis_age": age,
            "analysis_bmi": bmi,
        }

    def _build_smart_goal_data(
        self,
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
        selected_formula = self._resolve_body_fat_formula(body_fat_formula)
        if age is not None and age < 18:
            return {"available": False}
        gender = user.gender
        profile_weight = float(user.weight_kg) if user.weight_kg not in (None, "") else None
        profile_height = float(user.height_cm) if user.height_cm not in (None, "") else None

        def body_fat_for(circumference, caliper, height_cm, weight_kg, measurement_record):
            skinfolds = CaliperSkinfolds.from_model(caliper)
            measures = CircumferenceMeasures.from_model(circumference)
            measurement_age = self._age_at_measurement(user, measurement_record)
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
            paired = self._find_caliper_for_datetime(user, self._measurement_datetime(record), caliper_records=cal_records)
            if paired is not None:
                paired_caliper_ids.add(id(paired))
            weight = float(record.weight_kg) if record.weight_kg not in (None, "") else profile_weight
            height = float(record.height_cm) if record.height_cm not in (None, "") else profile_height
            body_fat, formula_name = body_fat_for(record, paired, height, weight, record)
            whr = self._whr_from_circumference(record)
            observations.append(
                {
                    "_sort_key": self._measurement_datetime(record),
                    "date": self._record_jalali_date(record),
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
            paired_circumference = self._find_circumference_for_datetime(
                user,
                self._measurement_datetime(record),
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
                    "_sort_key": self._measurement_datetime(record),
                    "date": self._record_jalali_date(record),
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

    def get_performance_chart_data(self, user: Any) -> dict[str, Any]:
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
            date_label = self._jalali_from_date(record.recorded_on)
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

    @staticmethod
    def _jalali_from_date(value) -> str:
        import jdatetime

        return jdatetime.date.fromgregorian(date=value).strftime("%Y/%m/%d")

    def _body_composition_observations(
        self,
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
                parsed = self._parse_jalali_date_obj(self._record_jalali_date(record))
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
            age = self._age_at_measurement(user, measurement_record)
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
                body_fat = self._body_fat_from_measurements(
                    caliper=caliper,
                    circumference=circumference,
                    height_cm=height,
                    age=age,
                    gender=gender,
                    formula=body_fat_formula,
                    weight_kg=weight,
                )
                if body_fat is None and body_fat_formula == "auto":
                    body_fat = self._body_fat_from_measurements(
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
                body_fat = self._body_fat_from_caliper(
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
                sort_key = self._parse_jalali_date_obj(label)
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

    def get_dashboard_body_composition_data(
        self,
        user: Any,
        *,
        body_fat_formula: str | None = None,
    ) -> dict[str, Any]:
        """Return the user's body-composition trend and same-gender site average."""
        from django.db.models import OuterRef, Q, Subquery

        from account.models import BodyCircumferenceMeasurement, CaliperMeasurement, User

        formula = self._resolve_body_fat_formula(body_fat_formula)
        circumference_records = list(user.circumference_records.all()[:120])
        caliper_records = list(user.caliper_records.all()[:120])
        observations = self._body_composition_observations(
            user,
            circumference_records=circumference_records,
            caliper_records=caliper_records,
            body_fat_formula=formula,
        )
        labels = [row["date"] for row in observations]
        body_fat_values = [row["body_fat"] for row in observations]
        lean_mass_values = [row["lean_mass"] for row in observations]

        peer_values: list[float] = []
        user_age = self._resolve_age(user)
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
                peer_observations = self._body_composition_observations(
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
        self,
        user: Any,
        *,
        body_fat_formula: str | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> dict[str, Any]:
        """Build the dashboard cards, history rows, and gauges for body analysis."""
        """JSON-ready time-series + gauges + history rows for the analysis dashboard (Chart.js)."""
        age = self._resolve_age(user)
        gender = user.gender
        activity_factor = activity_factor_for_level(user.activity_level)
        selected_body_fat_formula = self._resolve_body_fat_formula(body_fat_formula)

        circ_records_all = sorted(user.circumference_records.all()[:120], key=self._measurement_datetime)
        cal_records_all = sorted(user.caliper_records.all()[:120], key=self._measurement_datetime)
        range_start, range_end, start_j, end_j = self._resolve_analysis_date_range(user, start, end)

        def in_range(record):
            value = self._parse_jalali_date_obj(self._record_jalali_date(record))
            return start_j <= value <= end_j

        circ_records = [record for record in circ_records_all if in_range(record)]
        cal_records = [record for record in cal_records_all if in_range(record)]

        def jdate(record) -> str:
            return self._record_jalali_date(record)

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

        bmi_points = self._metric_series_points(
            user,
            "bmi",
            start_j,
            end_j,
            circumference_records=circ_records,
            caliper_records=cal_records,
        )
        bmi_labels = [point_date.strftime("%Y/%m/%d") for point_date, _ in bmi_points]
        bmi_values = [value for _, value in bmi_points]
        whr_points = self._metric_series_points(
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
                paired_caliper = self._find_caliper_for_datetime(user, self._measurement_datetime(record), caliper_records=cal_records)
                weight = record.weight_kg if record.weight_kg is not None else user.weight_kg
                height = record.height_cm if record.height_cm is not None else user.height_cm
                value = self._body_fat_from_measurements(
                    caliper=paired_caliper,
                    circumference=record,
                    height_cm=height,
                    age=self._age_at_measurement(user, record),
                    gender=gender,
                    formula=selected_body_fat_formula,
                    weight_kg=weight,
                )
                if value is not None:
                    fat_labels.append(jdate(record))
                    fat_values.append(round(float(value), 1))
        else:
            for record in cal_records:
                paired_circumference = self._find_circumference_for_datetime(
                    user,
                    self._measurement_datetime(record),
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
                value = self._body_fat_from_caliper(
                    record,
                    age=self._age_at_measurement(user, record),
                    gender=gender,
                    formula=selected_body_fat_formula,
                    weight_kg=weight,
                    height_cm=height,
                )
                if value is not None:
                    fat_labels.append(jdate(record))
                    fat_values.append(round(float(value), 1))
            for record in circ_records:
                measurement_age = self._age_at_measurement(user, record)
                if measurement_age is None or not 7 <= measurement_age < 18:
                    continue
                if self._find_caliper_for_datetime(
                    user,
                    self._measurement_datetime(record),
                    caliper_records=cal_records,
                ):
                    continue
                weight = record.weight_kg if record.weight_kg is not None else user.weight_kg
                height = record.height_cm if record.height_cm is not None else user.height_cm
                value = self._body_fat_from_measurements(
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
            paired = self._find_caliper_for_datetime(user, self._measurement_datetime(record), caliper_records=cal_records)
            fat = self._body_fat_from_measurements(
                caliper=paired,
                circumference=record,
                height_cm=height,
                age=self._age_at_measurement(user, record),
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
        whr_now = self._whr_from_circumference(latest_circ) if latest_circ else None

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

        smart_goal = self._build_smart_goal_data(
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
