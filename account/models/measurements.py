"""Body measurements and their provenance."""

from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .identity import User


class MeasurementSource(models.TextChoices):
    SELF_REPORTED = "self", _("ثبت توسط ورزشکار")
    COACH = "coach", _("ثبت توسط مربی")
    ADMIN = "admin", _("ثبت توسط ادمین")
    DEVICE = "device", _("ثبت توسط دستگاه")
    LEGACY = "legacy", _("داده قدیمی یا نامشخص")


class MeasurementMethod(models.TextChoices):
    TAPE = "tape", _("متر نواری")
    SKINFOLD_CALIPER = "caliper", _("کالیپر پوستی")
    SCALE = "scale", _("ترازو")
    BIOIMPEDANCE = "bioimpedance", _("آنالیز امپدانس")
    SELF_REPORTED = "self_reported", _("اظهاری")
    OTHER = "other", _("سایر")


class BodyCircumferenceMeasurement(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="circumference_records")
    measured_at = models.DateTimeField(default=timezone.now, db_index=True, verbose_name=_("زمان واقعی اندازه‌گیری"))
    measurement_source = models.CharField(
        max_length=12,
        choices=MeasurementSource.choices,
        default=MeasurementSource.LEGACY,
        verbose_name=_("منبع ثبت"),
    )
    measurement_method = models.CharField(
        max_length=20,
        choices=MeasurementMethod.choices,
        default=MeasurementMethod.OTHER,
        verbose_name=_("روش اندازه‌گیری"),
    )
    protocol_version = models.CharField(max_length=40, blank=True, verbose_name=_("نسخه پروتکل"))
    trial_number = models.PositiveSmallIntegerField(
        default=1,
        validators=[MinValueValidator(1)],
        verbose_name=_("شماره تکرار"),
    )
    measured_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recorded_circumference_measurements",
        verbose_name=_("ثبت‌کننده"),
    )
    height_cm = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True, verbose_name=_("قد (سانتی‌متر)"))
    weight_kg = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True, verbose_name=_("وزن (کیلوگرم)"))
    wrist_cm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("دور مچ دست"))
    forearm_cm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("دور ساعد"))
    arm_rest_cm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("دور بازو در حالت استراحت"))
    arm_flexed_cm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("دور بازو در حالت انقباض"))
    wrist_left_cm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("دور مچ دست (چپ)"))
    forearm_left_cm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("دور ساعد (چپ)"))
    arm_rest_left_cm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("دور بازو در حالت استراحت (چپ)"))
    arm_flexed_left_cm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("دور بازو در حالت انقباض (چپ)"))
    head_cm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("دور سر"))
    neck_cm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("دور گردن"))
    chest_cm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("دور قفسه سینه"))
    shoulders_cm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("دور شانه‌ها"))
    waist_cm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("دور کمر"))
    abdomen_cm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("دور شکم"))
    hips_cm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("دور باسن"))
    thigh_cm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("دور ران پا"))
    calf_cm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("دور ساق پا"))
    thigh_left_cm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("دور ران پا (چپ)"))
    calf_left_cm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("دور ساق پا (چپ)"))
    sit_height_cm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("قد نشسته"))
    leg_length_cm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("طول پا"))
    shoulder_width_cm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("عرض شانه"))
    hip_width_cm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("عرض لگن"))
    elbow_width_cm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("عرض آرنج"))
    knee_width_cm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("عرض زانو"))
    arm_length_cm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("طول بازو"))
    recorded_at = models.DateTimeField(auto_now_add=True)
    measured_at_jalali = models.CharField(max_length=10, null=True, blank=True, verbose_name=_("تاریخ ثبت اندازه‌گیری (شمسی)"))
    legacy_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ["-measured_at", "-pk"]
        verbose_name = "اندازه‌گیری محیط بدن"
        verbose_name_plural = "اندازه‌گیری‌های محیط بدن"
        indexes = [models.Index(fields=["user", "measured_at"])]
        constraints = [
            models.CheckConstraint(check=models.Q(trial_number__gte=1), name="circumference_trial_positive"),
        ]


class CaliperMeasurement(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="caliper_records")
    measured_at = models.DateTimeField(default=timezone.now, db_index=True, verbose_name=_("زمان واقعی اندازه‌گیری"))
    measurement_source = models.CharField(
        max_length=12,
        choices=MeasurementSource.choices,
        default=MeasurementSource.LEGACY,
        verbose_name=_("منبع ثبت"),
    )
    measurement_method = models.CharField(
        max_length=20,
        choices=MeasurementMethod.choices,
        default=MeasurementMethod.SKINFOLD_CALIPER,
        verbose_name=_("روش اندازه‌گیری"),
    )
    protocol_version = models.CharField(max_length=40, blank=True, verbose_name=_("نسخه پروتکل"))
    trial_number = models.PositiveSmallIntegerField(
        default=1,
        validators=[MinValueValidator(1)],
        verbose_name=_("شماره تکرار"),
    )
    measured_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recorded_caliper_measurements",
        verbose_name=_("ثبت‌کننده"),
    )
    chest_armpit_men_mm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("سینه"))
    axilla_mm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("زیربغل"))
    subscapular_mm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("تحت کتفی"))
    abdominal_mm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("شکم"))
    suprailiac_mm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("سه‌تیغ خاصره"))
    chest_mm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("کمر"))
    biceps_mm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("جلوبازو"))
    triceps_mm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("پشت بازو"))
    thigh_mm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("جلوی ران"))
    calf_mm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("پشت ساق پا"))
    recorded_at = models.DateTimeField(auto_now_add=True)
    measured_at_jalali = models.CharField(max_length=10, null=True, blank=True, verbose_name=_("تاریخ ثبت اندازه‌گیری (شمسی)"))
    legacy_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ["-measured_at", "-pk"]
        verbose_name = "اندازه‌گیری کالیپر"
        verbose_name_plural = "اندازه‌گیری‌های کالیپر"
        indexes = [models.Index(fields=["user", "measured_at"])]
        constraints = [
            models.CheckConstraint(check=models.Q(trial_number__gte=1), name="caliper_trial_positive"),
        ]
