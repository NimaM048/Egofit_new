"""Logged sessions, set outcomes, and progression records."""

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models.signals import pre_delete
from django.dispatch import receiver
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .identity import User
from .exercise_library import Exercise, CardioExercise
from .workout_programs import WorkoutProgram, WorkoutProgramDay, WorkoutProgramExercise


class WorkoutSession(models.Model):
    """One dated execution of a planned training day."""

    class Status(models.TextChoices):
        COMPLETED = "completed", _("انجام‌شده")
        PARTIAL = "partial", _("ناتمام")

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="workout_sessions", verbose_name=_("ورزشکار"))
    program = models.ForeignKey(
        WorkoutProgram,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="workout_sessions",
        verbose_name=_("برنامه"),
    )
    program_day = models.ForeignKey(
        WorkoutProgramDay,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="workout_sessions",
        verbose_name=_("روز برنامه"),
    )
    started_at = models.DateTimeField(default=timezone.now, db_index=True, verbose_name=_("زمان شروع"))
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name=_("زمان پایان"))
    duration_seconds = models.PositiveIntegerField(null=True, blank=True, verbose_name=_("مدت تمرین، ثانیه"))
    difficulty = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(10)],
        verbose_name=_("دشواری ادراک‌شده از ۱۰"),
    )
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.COMPLETED, db_index=True)
    notes = models.TextField(blank=True, verbose_name=_("یادداشت"))
    program_revision = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name=_("نسخه برنامه هنگام اجرا"))
    idempotency_key = models.CharField(max_length=64, null=True, blank=True, verbose_name=_("کلید جلوگیری از ثبت تکراری"))
    request_fingerprint = models.CharField(max_length=64, blank=True, verbose_name=_("اثر انگشت درخواست"))
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-started_at", "-id"]
        indexes = [models.Index(fields=["user", "started_at"]), models.Index(fields=["program", "started_at"])]
        constraints = [
            models.UniqueConstraint(fields=["user", "idempotency_key"], name="unique_workout_session_idempotency"),
            models.CheckConstraint(
                check=models.Q(difficulty__isnull=True) | models.Q(difficulty__lte=10),
                name="workout_session_difficulty_max_10",
            ),
            models.CheckConstraint(
                check=models.Q(duration_seconds__isnull=True) | models.Q(duration_seconds__lte=604800),
                name="workout_session_duration_max_7d",
            ),
            models.CheckConstraint(
                check=models.Q(completed_at__isnull=True) | models.Q(completed_at__gte=models.F("started_at")),
                name="workout_session_end_after_start",
            ),
        ]

    def clean(self):
        if self.program_day_id and self.program_id and self.program_day.program_id != self.program_id:
            raise ValidationError({"program_day": _("روز انتخاب‌شده متعلق به این برنامه نیست.")})

    def __str__(self):
        return f"{self.user.fullname} - {self.started_at:%Y-%m-%d}"


class WorkoutSetResult(models.Model):
    """Observed set-level outcome kept separately from the prescription."""

    class Slot(models.TextChoices):
        MAIN = "main", _("حرکت اصلی")
        SUPERSET = "superset", _("سوپرست")
        THIRD = "third", _("حرکت سوم")
        CARDIO = "cardio", _("هوازی")

    session = models.ForeignKey(WorkoutSession, on_delete=models.CASCADE, related_name="sets", verbose_name=_("جلسه"))
    program_exercise = models.ForeignKey(
        WorkoutProgramExercise,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="session_results",
        verbose_name=_("حرکت برنامه"),
    )
    exercise = models.ForeignKey(
        Exercise,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="session_results",
        verbose_name=_("حرکت مقاومتی"),
    )
    cardio_exercise = models.ForeignKey(
        CardioExercise,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="session_results",
        verbose_name=_("حرکت هوازی"),
    )
    slot = models.CharField(max_length=12, choices=Slot.choices, default=Slot.MAIN)
    set_number = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)], verbose_name=_("شماره ست"))
    planned_reps = models.CharField(max_length=60, blank=True, verbose_name=_("تکرار برنامه‌ریزی‌شده"))
    planned_movement_name = models.CharField(max_length=160, blank=True, verbose_name=_("نام حرکت هنگام اجرا"))
    planned_sets_min = models.PositiveSmallIntegerField(null=True, blank=True)
    planned_sets_max = models.PositiveSmallIntegerField(null=True, blank=True)
    planned_reps_min = models.PositiveSmallIntegerField(null=True, blank=True)
    planned_reps_max = models.PositiveSmallIntegerField(null=True, blank=True)
    actual_reps = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name=_("تکرار انجام‌شده"))
    planned_load_kg = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    planned_load_percent_1rm = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    planned_rpe = models.PositiveSmallIntegerField(null=True, blank=True)
    planned_rir = models.PositiveSmallIntegerField(null=True, blank=True)
    planned_rest_seconds_min = models.PositiveSmallIntegerField(null=True, blank=True)
    planned_rest_seconds_max = models.PositiveSmallIntegerField(null=True, blank=True)
    planned_duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    planned_interval_count = models.PositiveSmallIntegerField(null=True, blank=True)
    planned_work_seconds = models.PositiveSmallIntegerField(null=True, blank=True)
    planned_recovery_seconds = models.PositiveSmallIntegerField(null=True, blank=True)
    target_heart_rate_min = models.PositiveSmallIntegerField(null=True, blank=True)
    target_heart_rate_max = models.PositiveSmallIntegerField(null=True, blank=True)
    actual_load_kg = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    duration_seconds = models.PositiveIntegerField(null=True, blank=True, verbose_name=_("مدت، ثانیه"))
    distance_m = models.DecimalField(max_digits=9, decimal_places=2, null=True, blank=True, verbose_name=_("مسافت، متر"))
    average_heart_rate_bpm = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name=_("میانگین ضربان قلب"))
    peak_heart_rate_bpm = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name=_("بیشینه ضربان قلب"))
    rpe = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MinValueValidator(0), MaxValueValidator(10)])
    rir = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MinValueValidator(0), MaxValueValidator(10)])
    completed = models.BooleanField(default=True, verbose_name=_("ست انجام شد"))
    is_warmup = models.BooleanField(default=False, verbose_name=_("ست گرم‌کردن"))
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["session_id", "program_exercise_id", "slot", "set_number", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "program_exercise", "slot", "set_number", "is_warmup"],
                name="unique_session_program_slot_set_kind",
            ),
            models.CheckConstraint(
                check=(
                    models.Q(exercise__isnull=False, cardio_exercise__isnull=True)
                    | models.Q(exercise__isnull=True, cardio_exercise__isnull=False)
                ),
                name="workout_set_one_movement",
            ),
            models.CheckConstraint(check=models.Q(set_number__gte=1), name="workout_set_number_positive"),
            models.CheckConstraint(
                check=models.Q(actual_reps__isnull=True) | models.Q(actual_reps__lte=1000),
                name="workout_set_reps_max_1000",
            ),
            models.CheckConstraint(
                check=models.Q(duration_seconds__isnull=True) | models.Q(duration_seconds__lte=604800),
                name="workout_set_duration_max_7d",
            ),
            models.CheckConstraint(check=models.Q(rpe__isnull=True) | models.Q(rpe__lte=10), name="workout_set_rpe_max_10"),
            models.CheckConstraint(check=models.Q(rir__isnull=True) | models.Q(rir__lte=10), name="workout_set_rir_max_10"),
            models.CheckConstraint(
                check=models.Q(actual_load_kg__isnull=True) | models.Q(actual_load_kg__gte=0),
                name="workout_set_load_nonnegative",
            ),
            models.CheckConstraint(
                check=models.Q(planned_sets_min__isnull=True) | models.Q(planned_sets_max__isnull=True) | models.Q(planned_sets_max__gte=models.F("planned_sets_min")),
                name="workout_set_planned_sets_range_valid",
            ),
            models.CheckConstraint(
                check=models.Q(planned_reps_min__isnull=True) | models.Q(planned_reps_max__isnull=True) | models.Q(planned_reps_max__gte=models.F("planned_reps_min")),
                name="workout_set_planned_reps_range_valid",
            ),
            models.CheckConstraint(
                check=models.Q(target_heart_rate_min__isnull=True) | models.Q(target_heart_rate_max__isnull=True) | models.Q(target_heart_rate_max__gte=models.F("target_heart_rate_min")),
                name="workout_set_planned_hr_range_valid",
            ),
            models.CheckConstraint(
                check=models.Q(distance_m__isnull=True) | models.Q(distance_m__gt=0),
                name="workout_set_distance_positive",
            ),
            models.CheckConstraint(
                check=models.Q(average_heart_rate_bpm__isnull=True) | models.Q(average_heart_rate_bpm__gte=30, average_heart_rate_bpm__lte=240),
                name="workout_set_average_hr_valid",
            ),
            models.CheckConstraint(
                check=models.Q(peak_heart_rate_bpm__isnull=True) | models.Q(peak_heart_rate_bpm__gte=30, peak_heart_rate_bpm__lte=240),
                name="workout_set_peak_hr_valid",
            ),
            models.CheckConstraint(
                check=models.Q(average_heart_rate_bpm__isnull=True) | models.Q(peak_heart_rate_bpm__isnull=True) | models.Q(peak_heart_rate_bpm__gte=models.F("average_heart_rate_bpm")),
                name="workout_set_peak_hr_after_average",
            ),
        ]
        indexes = [models.Index(fields=["session", "exercise", "set_number"])]

    def clean(self):
        if self.program_exercise_id and self.session_id:
            if self.session.program_day_id and self.program_exercise.day_id != self.session.program_day_id:
                raise ValidationError({"program_exercise": _("حرکت متعلق به روز ثبت‌شده نیست.")})
        if self.exercise_id and self.cardio_exercise_id:
            raise ValidationError(_("برای هر ست فقط یک حرکت انتخاب کنید."))

    def __str__(self):
        return f"{self.session} - {self.get_slot_display()} - {self.set_number}"


class TrainingAdjustmentRecommendation(models.Model):
    class RecommendationType(models.TextChoices):
        INCREASE_LOAD = "increase_load", _("بررسی افزایش بار")
        HOLD = "hold", _("حفظ نسخه فعلی")
        REVIEW = "review", _("نیازمند بررسی مربی")
        DELOAD = "deload", _("پیشنهاد هفته سبک")

    class Confidence(models.TextChoices):
        LOW = "low", _("پایین")
        MEDIUM = "medium", _("متوسط")
        HIGH = "high", _("بالا")

    class Status(models.TextChoices):
        PENDING = "pending", _("در انتظار بازبینی")
        ACCEPTED = "accepted", _("تأیید شد")
        EDITED = "edited", _("با اصلاح مربی ثبت شد")
        REJECTED = "rejected", _("رد شد")
        SUPERSEDED = "superseded", _("با پیشنهاد جدیدتر ادغام شد")

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="training_adjustment_recommendations")
    program = models.ForeignKey(WorkoutProgram, on_delete=models.SET_NULL, null=True, blank=True, related_name="training_recommendations")
    source_session = models.ForeignKey(WorkoutSession, on_delete=models.CASCADE, related_name="progression_recommendations")
    program_exercise = models.ForeignKey(WorkoutProgramExercise, on_delete=models.SET_NULL, null=True, blank=True, related_name="progression_recommendations")
    exercise = models.ForeignKey(Exercise, on_delete=models.PROTECT, related_name="training_recommendations")
    movement_name = models.CharField(max_length=160)
    slot = models.CharField(max_length=12, choices=WorkoutSetResult.Slot.choices, default=WorkoutSetResult.Slot.MAIN)
    recommendation_type = models.CharField(max_length=20, choices=RecommendationType.choices)
    confidence = models.CharField(max_length=10, choices=Confidence.choices, default=Confidence.LOW)
    baseline_load_kg = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    suggested_load_kg = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    reviewed_load_kg = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    rationale = models.TextField()
    evidence = models.JSONField(default=dict, blank=True)
    algorithm_version = models.CharField(max_length=32, default="progression-v1")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True)
    reviewed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="reviewed_training_recommendations")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    coach_note = models.TextField(blank=True)
    superseded_by = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="superseded_recommendations",
    )
    applied_to_program = models.ForeignKey(
        "WorkoutProgram",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="applied_progression_recommendations",
    )
    applied_at = models.DateTimeField(null=True, blank=True)
    applied_program_revision = models.PositiveSmallIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["source_session", "exercise", "slot", "recommendation_type"],
                name="unique_session_training_recommendation_type",
            ),
            models.CheckConstraint(check=models.Q(baseline_load_kg__isnull=True) | models.Q(baseline_load_kg__gte=0), name="training_recommendation_baseline_nonnegative"),
            models.CheckConstraint(check=models.Q(suggested_load_kg__isnull=True) | models.Q(suggested_load_kg__gte=0), name="training_recommendation_suggested_nonnegative"),
            models.CheckConstraint(check=models.Q(reviewed_load_kg__isnull=True) | models.Q(reviewed_load_kg__gte=0), name="training_recommendation_reviewed_nonnegative"),
        ]
        indexes = [models.Index(fields=["user", "status", "created_at"])]

    def __str__(self):
        return f"{self.movement_name} - {self.get_recommendation_type_display()}"


class ExerciseStrengthEstimate(models.Model):
    """Immutable estimated 1RM samples derived from completed working sets."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="exercise_strength_estimates")
    exercise = models.ForeignKey(Exercise, on_delete=models.CASCADE, related_name="strength_estimates")
    source_set = models.OneToOneField(
        WorkoutSetResult,
        on_delete=models.CASCADE,
        related_name="strength_estimate",
    )
    e1rm_kg = models.DecimalField(max_digits=8, decimal_places=2, validators=[MinValueValidator(0.01)])
    effective_reps = models.DecimalField(max_digits=5, decimal_places=2)
    measured_on = models.DateTimeField(db_index=True)
    algorithm_version = models.CharField(max_length=24, default="epley-rir-v1")

    class Meta:
        ordering = ["-measured_on", "-pk"]
        indexes = [models.Index(fields=["user", "exercise", "measured_on"])]

    def __str__(self):
        return f"{self.user_id}:{self.exercise_id} - {self.e1rm_kg} kg"


class WorkoutPerformanceRecord(models.Model):
    class Mode(models.TextChoices):
        WEIGHT = "weight", _("وزنه (کیلوگرم)")
        BODY_WEIGHT = "body_weight", _("وزن بدن")
        TIME = "time", _("زمان (ثانیه)")

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="workout_performance_records",
        verbose_name=_("ورزشکار"),
    )
    exercise = models.ForeignKey(
        Exercise,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="workout_performance_records",
        verbose_name=_("حرکت"),
    )
    exercise_name_snapshot = models.CharField(max_length=120, blank=True, verbose_name=_("نام حرکت"))
    program = models.ForeignKey(
        WorkoutProgram,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="performance_records",
        verbose_name=_("برنامه"),
    )
    program_exercise = models.ForeignKey(
        WorkoutProgramExercise,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="performance_records",
        verbose_name=_("حرکت برنامه"),
    )
    repetitions = models.CharField(max_length=60, verbose_name=_("تعداد تکرار برنامه"))
    mode = models.CharField(
        max_length=20,
        choices=Mode.choices,
        verbose_name=_("نحوه ثبت عملکرد"),
    )
    set_number = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1)],
        verbose_name=_("شماره ست"),
    )
    value = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        validators=[MinValueValidator(0)],
        verbose_name=_("مقدار ثبت‌شده"),
    )
    recorded_on = models.DateField(
        default=timezone.localdate,
        verbose_name=_("تاریخ ثبت"),
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("زمان ایجاد"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("آخرین به‌روزرسانی"))

    class Meta:
        verbose_name = "رکورد عملکرد حرکت"
        verbose_name_plural = "رکوردهای عملکرد حرکت"
        ordering = ["-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "user",
                    "exercise",
                    "repetitions",
                    "mode",
                    "set_number",
                    "recorded_on",
                ],
                name="unique_workout_performance_record",
            ),
            models.CheckConstraint(
                check=models.Q(value__gt=0),
                name="workout_performance_value_positive",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "exercise", "mode"]),
            models.Index(fields=["user", "exercise", "repetitions"]),
            models.Index(fields=["user", "recorded_on"]),
        ]

    def clean(self):
        if self.mode not in self.Mode.values:
            raise ValidationError({"mode": _("نحوه ثبت عملکرد معتبر نیست.")})
        if self.value is not None and self.value <= 0:
            raise ValidationError({"value": _("مقدار ثبت‌شده باید بیشتر از صفر باشد.")})

    @property
    def unit_label(self):
        return {
            self.Mode.WEIGHT: _("کیلوگرم"),
            self.Mode.BODY_WEIGHT: _("کیلوگرم"),
            self.Mode.TIME: _("ثانیه"),
        }.get(self.mode, "")

    @property
    def exercise_label(self):
        return self.exercise.name if self.exercise_id else self.exercise_name_snapshot


class WorkoutBestRecord(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="workout_best_records",
        verbose_name=_("ورزشکار"),
    )
    exercise = models.ForeignKey(
        Exercise,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="workout_best_records",
        verbose_name=_("حرکت"),
    )
    exercise_name_snapshot = models.CharField(max_length=120, blank=True, verbose_name=_("نام حرکت"))
    program = models.ForeignKey(
        WorkoutProgram,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="best_records",
        verbose_name=_("برنامه"),
    )
    program_exercise = models.ForeignKey(
        WorkoutProgramExercise,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="best_records",
        verbose_name=_("حرکت برنامه"),
    )
    value = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        validators=[MinValueValidator(0)],
        verbose_name=_("بهترین رکورد (کیلوگرم)"),
    )
    recorded_at = models.DateTimeField(auto_now=True, verbose_name=_("تاریخ ثبت"))

    class Meta:
        verbose_name = "بهترین رکورد حرکت"
        verbose_name_plural = "بهترین رکوردهای حرکات"
        ordering = ["-recorded_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "exercise"],
                name="unique_workout_best_record",
            ),
            models.CheckConstraint(
                check=models.Q(value__gt=0),
                name="workout_best_record_value_positive",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "exercise"], name="account_wor_user_id_4d1f6e_idx"),
            models.Index(fields=["user", "recorded_at"], name="account_wor_user_id_9a1c3b_idx"),
        ]

    def clean(self):
        if self.value is not None and self.value <= 0:
            raise ValidationError({"value": _("مقدار رکورد باید بیشتر از صفر باشد.")})

    def __str__(self):
        return f"{self.user.fullname} - {self.exercise_label} - {self.value}"

    @property
    def exercise_label(self):
        return self.exercise.name if self.exercise_id else self.exercise_name_snapshot


@receiver(pre_delete, sender=Exercise)
def preserve_exercise_names_in_workout_records(sender, instance, **kwargs):
    WorkoutPerformanceRecord.objects.filter(exercise=instance).update(
        exercise_name_snapshot=instance.name
    )
    WorkoutBestRecord.objects.filter(exercise=instance).update(
        exercise_name_snapshot=instance.name
    )
