"""Health history, daily check-ins, reminders, and coach alerts."""

import datetime

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .identity import User
from .measurements import MeasurementSource


def default_mood_reminder_weekdays():
    return [0, 2, 4]


class UserHealthRecord(models.Model):
    class CategoryChoices(models.TextChoices):
        DISEASE = "disease", _("بیماری")
        ABNORMALITY = "abnormality", _("ناهنجاری")
        INJURY = "injury", _("آسیب")
        MEDICATION = "medication", _("دارو")
        ALLERGY = "allergy", _("حساسیت")

    class BodyRegion(models.TextChoices):
        HEAD_NECK = "head_neck", _("سر و گردن")
        SHOULDER = "shoulder", _("شانه")
        ARM = "arm", _("بازو و دست")
        BACK = "back", _("پشت و کمر")
        HIP = "hip", _("لگن")
        LEG = "leg", _("پا")
        CHEST = "chest", _("قفسه سینه")
        GENERAL = "general", _("عمومی")
        OTHER = "other", _("سایر")

    class Side(models.TextChoices):
        LEFT = "left", _("چپ")
        RIGHT = "right", _("راست")
        BOTH = "both", _("هر دو سمت")
        NOT_APPLICABLE = "na", _("نامرتبط")

    class Status(models.TextChoices):
        ACTIVE = "active", _("فعال")
        RESOLVED = "resolved", _("برطرف‌شده")

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="health_records", verbose_name=_("کاربر"))
    category = models.CharField(max_length=20, choices=CategoryChoices.choices, verbose_name=_("دسته‌بندی"))
    title = models.CharField(max_length=160, verbose_name=_("عنوان"))
    abnormality_type = models.ForeignKey(
        "ExerciseAbnormalityType",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="health_records",
        verbose_name=_("ناهنجاری"),
    )
    details = models.TextField(blank=True, verbose_name=_("جزئیات"))
    body_region = models.CharField(max_length=20, choices=BodyRegion.choices, blank=True, verbose_name=_("ناحیه بدن"))
    affected_side = models.CharField(max_length=8, choices=Side.choices, blank=True, verbose_name=_("سمت"))
    severity = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(10)],
        verbose_name=_("شدت گزارش‌شده از ۱۰"),
    )
    onset_date = models.DateField(null=True, blank=True, verbose_name=_("تاریخ شروع"))
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE, db_index=True, verbose_name=_("وضعیت"))
    resolved_date = models.DateField(null=True, blank=True, verbose_name=_("تاریخ برطرف‌شدن"))
    training_restriction = models.BooleanField(default=False, verbose_name=_("محدودیت تمرینی دارد"))
    training_restriction_notes = models.TextField(blank=True, verbose_name=_("توضیح محدودیت تمرینی"))
    recorded_at = models.DateField(default=timezone.localdate, verbose_name=_("تاریخ ثبت"))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-recorded_at", "-id"]
        verbose_name = _("سابقه سلامت کاربر")
        verbose_name_plural = _("سوابق سلامت کاربران")
        indexes = [
            models.Index(fields=["user", "category"]),
            models.Index(fields=["user", "recorded_at"]),
            models.Index(fields=["user", "status", "category"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(severity__isnull=True) | models.Q(severity__lte=10),
                name="user_health_severity_max_10",
            ),
            models.CheckConstraint(
                check=(
                    models.Q(resolved_date__isnull=True)
                    | models.Q(onset_date__isnull=True)
                    | models.Q(resolved_date__gte=models.F("onset_date"))
                ),
                name="user_health_resolution_after_onset",
            ),
        ]

    def __str__(self):
        return f"{self.user.fullname} - {self.get_category_display()} - {self.title}"


class DailyMoodEntry(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="mood_entries", verbose_name=_("کاربر"))
    entry_date = models.DateField(default=timezone.localdate, verbose_name=_("تاریخ"))
    anxiety_score = models.PositiveSmallIntegerField(validators=[MinValueValidator(0), MaxValueValidator(10)], verbose_name=_("میزان اضطراب"))
    motivation_score = models.PositiveSmallIntegerField(validators=[MinValueValidator(0), MaxValueValidator(10)], verbose_name=_("میزان انگیزه"))
    sleep_quality_score = models.PositiveSmallIntegerField(validators=[MinValueValidator(0), MaxValueValidator(10)], verbose_name=_("کیفیت خواب"))
    training_satisfaction_score = models.PositiveSmallIntegerField(validators=[MinValueValidator(0), MaxValueValidator(10)], verbose_name=_("رضایت از کیفیت تمرین"))
    training_readiness_score = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(10)],
        verbose_name=_("آمادگی تمرین"),
        help_text=_("آمادگی جسمی امروز را از ۰ تا ۱۰ ثبت کنید؛ این داده فقط برای تعدیل محافظه‌کارانه برنامه استفاده می‌شود."),
    )
    fatigue_score = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(10)],
        verbose_name=_("خستگی"),
        help_text=_("۰ یعنی بدون خستگی و ۱۰ یعنی خستگی بسیار زیاد."),
    )
    muscle_soreness_score = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(10)],
        verbose_name=_("کوفتگی عضلانی"),
        help_text=_("۰ یعنی بدون کوفتگی و ۱۰ یعنی کوفتگی بسیار زیاد."),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-entry_date", "-id"]
        verbose_name = _("ثبت روحیات روزانه")
        verbose_name_plural = _("ثبت روحیات روزانه")
        constraints = [models.UniqueConstraint(fields=["user", "entry_date"], name="unique_user_mood_entry_date")]
        indexes = [models.Index(fields=["user", "entry_date"])]

    def __str__(self):
        return f"{self.user.fullname} - {self.entry_date}"


class DailyHeartRateEntry(models.Model):
    class MeasurementContext(models.TextChoices):
        RESTING_MORNING = "resting_morning", _("ضربان استراحت صبحگاهی")
        DURING_TRAINING = "during_training", _("حین تمرین")
        RECOVERY = "recovery", _("بازیابی پس از تمرین")
        UNKNOWN = "unknown", _("نامشخص")

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="heart_rate_entries", verbose_name=_("کاربر"))
    entry_date = models.DateField(default=timezone.localdate, verbose_name=_("تاریخ"))
    measured_at = models.DateTimeField(null=True, blank=True, verbose_name=_("زمان واقعی سنجش"))
    bpm = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(30), MaxValueValidator(220)],
        verbose_name=_("ضربان قلب (ضربان در دقیقه)"),
    )
    source = models.CharField(max_length=12, choices=MeasurementSource.choices, default=MeasurementSource.LEGACY, verbose_name=_("منبع ثبت"))
    measurement_context = models.CharField(max_length=20, choices=MeasurementContext.choices, default=MeasurementContext.UNKNOWN, verbose_name=_("زمینه سنجش"))
    device_name = models.CharField(max_length=100, blank=True, verbose_name=_("دستگاه سنجش"))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-entry_date", "-id"]
        verbose_name = _("ثبت ضربان قلب روزانه")
        verbose_name_plural = _("ثبت ضربان قلب روزانه")
        constraints = [models.UniqueConstraint(fields=["user", "entry_date"], name="unique_user_heart_rate_entry_date")]
        indexes = [models.Index(fields=["user", "entry_date"])]

    def __str__(self):
        return f"{self.user.fullname} - {self.entry_date} - {self.bpm} BPM"


class MoodReminderPreference(models.Model):
    """A user's weekly schedule and browser push preference for mood logging."""

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="mood_reminder_preference",
        verbose_name=_("کاربر"),
    )
    enabled = models.BooleanField(default=False, db_index=True, verbose_name=_("یادآور فعال است"))
    # Weekday indexes use a Saturday-first week: Saturday=0 ... Friday=6.
    weekdays = models.JSONField(default=default_mood_reminder_weekdays, verbose_name=_("روزهای یادآوری"))
    remind_at = models.TimeField(default=datetime.time(20, 0), verbose_name=_("ساعت یادآوری"))
    time_zone = models.CharField(max_length=64, default="Asia/Tehran", verbose_name=_("منطقه زمانی"))
    prompt_deferred_until = models.DateTimeField(null=True, blank=True, verbose_name=_("زمان پیشنهاد دوباره"))
    last_sent_local_date = models.DateField(null=True, blank=True, verbose_name=_("آخرین تاریخ ارسال"))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("تنظیم یادآور ثبت روحیات")
        verbose_name_plural = _("تنظیم یادآور ثبت روحیات")

    def __str__(self):
        return f"{self.user.fullname} - mood reminder"


class MoodReminderSubscription(models.Model):
    """One browser/device endpoint registered for Web Push delivery."""

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="mood_reminder_subscriptions",
        verbose_name=_("کاربر"),
    )
    endpoint = models.TextField(verbose_name=_("نشانی اشتراک مرورگر"))
    endpoint_hash = models.CharField(max_length=64, unique=True, verbose_name=_("شناسه یکتای اشتراک مرورگر"))
    p256dh = models.CharField(max_length=256, verbose_name=_("کلید رمزنگاری مرورگر"))
    auth = models.CharField(max_length=128, verbose_name=_("کلید احراز هویت مرورگر"))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("اشتراک اعلان مرورگر")
        verbose_name_plural = _("اشتراک‌های اعلان مرورگر")

    def __str__(self):
        return f"{self.user.fullname} - push subscription"


class CoachHealthAlertPreference(models.Model):
    """Whether a coach wants alerts when an assigned athlete's health trends worsen."""

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="health_alert_preference",
        verbose_name=_("مربی"),
    )
    enabled = models.BooleanField(default=True, db_index=True, verbose_name=_("اعلان تغییرات وضعیت شاگرد فعال است"))
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("تنظیم اعلان تغییرات سلامت شاگرد")
        verbose_name_plural = _("تنظیم‌های اعلان تغییرات سلامت شاگرد")

    def __str__(self):
        return f"{self.user.fullname} - health alerts {'on' if self.enabled else 'off'}"


class AthleteHealthAlert(models.Model):
    """A recipient-specific snapshot of worsening mood or resting heart-rate trends."""

    recipient = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="received_health_alerts",
        verbose_name=_("دریافت‌کننده"),
    )
    athlete = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="health_alerts",
        verbose_name=_("شاگرد"),
    )
    observed_date = models.DateField(verbose_name=_("تاریخ بررسی"))
    changes = models.JSONField(default=list, verbose_name=_("تغییرات وضعیت در دو ماه گذشته"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("زمان اعلان"))
    reviewed_at = models.DateTimeField(null=True, blank=True, verbose_name=_("زمان بررسی"))

    class Meta:
        verbose_name = _("اعلان تغییر وضعیت شاگرد")
        verbose_name_plural = _("اعلان‌های تغییر وضعیت شاگرد")
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["recipient", "athlete", "observed_date"],
                name="unique_health_alert_recipient_athlete_day",
            ),
        ]
        indexes = [models.Index(fields=["recipient", "reviewed_at", "created_at"])]

    def __str__(self):
        return f"{self.athlete.fullname} - {self.observed_date}"
