"""Prescribed workout plans, days, templates, and progress."""

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from account.domain.exercise_goals import canonical_goal_key

from .identity import User
from .exercise_library import ExerciseDifficultyLevel, Exercise, CorrectiveExercise, CardioExercise
from .training_profiles import TrainingGoal


class WorkoutProgram(models.Model):
    class ProgramType(models.TextChoices):
        NORMAL = "normal", "Normal"
        FULL_BODY = "full_body", "Full Body"
        PUSH_PULL = "push_pull", "Push/Pull"
        SPLIT = "split", "Split"
        PUSH_PULL_LEGS = "push_pull_legs", "Push/Pull/Leg"

    title = models.CharField(max_length=150, verbose_name=_("عنوان برنامه"))
    program_type = models.CharField(
        max_length=20,
        choices=ProgramType.choices,
        default=ProgramType.NORMAL,
        blank=True,
        verbose_name=_("نوع برنامه"),
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="workout_programs",
        verbose_name=_("ورزشکار"),
    )
    prescribed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="prescribed_workout_programs",
        verbose_name=_("تجویزکننده"),
    )
    start_date = models.DateField(
        default=timezone.localdate,
        verbose_name=_("تاریخ شروع"),
    )
    end_date = models.DateField(
        null=True,
        blank=True,
        verbose_name=_("تاریخ پایان"),
    )
    is_published = models.BooleanField(default=True, verbose_name=_("نمایش برای کاربر"))
    requires_payment = models.BooleanField(default=False, verbose_name=_("نیازمند پرداخت"))
    price = models.PositiveIntegerField(default=0, verbose_name=_("هزینه (تومان)"))
    notes = models.TextField(blank=True, verbose_name=_("توضیحات برنامه"))
    supplements_note = models.TextField(blank=True, verbose_name=_("مکمل و نکات تغذیه‌ای"))
    warmup_notes = models.TextField(blank=True, verbose_name=_("توضیحات گرم کردن"))
    cooldown_notes = models.TextField(blank=True, verbose_name=_("توضیحات سرد کردن"))
    revision = models.PositiveSmallIntegerField(default=1, verbose_name=_("نسخه برنامه"))
    algorithm_version = models.CharField(max_length=32, blank=True, verbose_name=_("نسخه الگوریتم"))
    generation_seed = models.CharField(max_length=64, blank=True, verbose_name=_("بذر تولید"))
    generation_metadata = models.JSONField(default=dict, blank=True, verbose_name=_("مبنای تولید برنامه"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("تاریخ ایجاد"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("آخرین ویرایش"))

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "برنامه بدنسازی"
        verbose_name_plural = "برنامه‌های بدنسازی"
        indexes = [
            models.Index(fields=["user", "is_published", "start_date"]),
            models.Index(fields=["prescribed_by", "created_at"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(requires_payment=False, price=0)
                    | models.Q(requires_payment=True, price__gt=0)
                ),
                name="workout_program_access_price_consistent",
            ),
        ]

    def clean(self):
        if self.end_date and self.start_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": _("تاریخ پایان نمی‌تواند قبل از تاریخ شروع باشد.")})

    @property
    def duration_weeks(self):
        if not self.start_date or not self.end_date:
            return 1
        duration_days = max(1, (self.end_date - self.start_date).days + 1)
        return max(1, (duration_days + 6) // 7)

    def __str__(self):
        return f"{self.title} - {self.user.fullname}"


class WorkoutProgramPayment(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", _("در انتظار پرداخت")
        INITIATED = "initiated", _("پرداخت آغاز شده")
        PAID = "paid", _("پرداخت شده")
        FAILED = "failed", _("ناموفق")

    program = models.ForeignKey(
        WorkoutProgram,
        on_delete=models.CASCADE,
        related_name="payments",
        verbose_name=_("برنامه"),
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="workout_program_payments",
        verbose_name=_("کاربر"),
    )
    responsible = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="responsible_workout_program_payments",
        limit_choices_to=models.Q(is_admin=True) | models.Q(is_coach=True),
        verbose_name=_("مسئول درآمد"),
    )
    amount = models.PositiveIntegerField(verbose_name=_("مبلغ"))
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
        verbose_name=_("وضعیت"),
    )
    authority = models.CharField(max_length=64, unique=True, null=True, blank=True)
    ref_id = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("پرداخت برنامه بدنسازی")
        verbose_name_plural = _("پرداخت‌های برنامه بدنسازی")
        indexes = [
            models.Index(fields=["status", "paid_at", "responsible"], name="acc_wpp_stat_paid_resp"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["program", "user"],
                condition=models.Q(status="initiated"),
                name="unique_active_workout_payment",
            ),
        ]


class WorkoutProgramDay(models.Model):
    class Weekday(models.TextChoices):
        SATURDAY = "sat", _("شنبه")
        SUNDAY = "sun", _("یکشنبه")
        MONDAY = "mon", _("دوشنبه")
        TUESDAY = "tue", _("سه‌شنبه")
        WEDNESDAY = "wed", _("چهارشنبه")
        THURSDAY = "thu", _("پنجشنبه")
        FRIDAY = "fri", _("جمعه")

    program = models.ForeignKey(
        WorkoutProgram,
        on_delete=models.CASCADE,
        related_name="days",
        verbose_name=_("برنامه"),
    )
    name = models.CharField(max_length=80, verbose_name=_("عنوان روز"))
    order = models.PositiveSmallIntegerField(default=1, verbose_name=_("ترتیب"))
    notes = models.TextField(blank=True, verbose_name=_("توضیحات روز"))
    scheduled_weekday = models.CharField(
        max_length=3,
        choices=Weekday.choices,
        blank=True,
        verbose_name=_("روز پیشنهادی هفته"),
    )
    estimated_duration_minutes = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(600)],
        verbose_name=_("مدت برآوردی جلسه، دقیقه"),
    )

    class Meta:
        ordering = ["order", "id"]
        verbose_name = "روز برنامه بدنسازی"
        verbose_name_plural = "روزهای برنامه بدنسازی"
        constraints = [
            models.UniqueConstraint(fields=["program", "order"], name="unique_workout_program_day_order"),
        ]

    def __str__(self):
        return f"{self.program.title} - {self.name}"


class WorkoutProgramDayProgress(models.Model):
    day = models.OneToOneField(
        WorkoutProgramDay,
        on_delete=models.CASCADE,
        related_name="progress",
        verbose_name=_("جلسه برنامه"),
    )
    completed_count = models.PositiveIntegerField(default=0, verbose_name=_("تعداد دفعات انجام‌شده"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("آخرین به‌روزرسانی"))

    class Meta:
        verbose_name = "پیشرفت جلسه برنامه"
        verbose_name_plural = "پیشرفت جلسات برنامه"

    def __str__(self):
        return f"{self.day} - {self.completed_count}"


class WorkoutProgramExercise(models.Model):
    day = models.ForeignKey(
        WorkoutProgramDay,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name=_("روز برنامه"),
    )
    exercise = models.ForeignKey(
        Exercise,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="program_items",
        verbose_name=_("حرکت اصلی"),
    )
    cardio_exercise = models.ForeignKey(
        CardioExercise,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="program_items",
        verbose_name=_("حرکت هوازی"),
    )
    superset_exercise = models.ForeignKey(
        Exercise,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="superset_program_items",
        verbose_name=_("حرکت دوم سوپرست"),
    )
    third_exercise = models.ForeignKey(
        Exercise,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="third_program_items",
        verbose_name=_("حرکت سوم"),
    )
    goal = models.CharField(max_length=80, blank=True, verbose_name=_("هدف حرکت"))
    sets = models.CharField(max_length=40, verbose_name=_("تعداد ست"))
    reps = models.CharField(max_length=60, blank=True, verbose_name=_("تعداد تکرار"))
    rest = models.CharField(max_length=40, blank=True, verbose_name=_("استراحت"))
    superset_sets = models.CharField(
        max_length=40,
        null=True,
        blank=True,
        verbose_name=_("تعداد ست حرکت دوم"),
    )
    superset_reps = models.CharField(
        max_length=60,
        null=True,
        blank=True,
        verbose_name=_("تعداد تکرار حرکت دوم"),
    )
    superset_rest = models.CharField(
        max_length=40,
        null=True,
        blank=True,
        verbose_name=_("استراحت حرکت دوم"),
    )
    third_sets = models.CharField(
        max_length=40,
        null=True,
        blank=True,
        verbose_name=_("تعداد ست حرکت سوم"),
    )
    third_reps = models.CharField(
        max_length=60,
        null=True,
        blank=True,
        verbose_name=_("تعداد تکرار حرکت سوم"),
    )
    third_rest = models.CharField(
        max_length=40,
        null=True,
        blank=True,
        verbose_name=_("استراحت حرکت سوم"),
    )
    note = models.TextField(blank=True, verbose_name=_("نکته حرکتی"))
    order = models.PositiveSmallIntegerField(default=1, verbose_name=_("ترتیب"))
    performance_modes = models.JSONField(
        default=dict,
        blank=True,
        verbose_name=_("نحوه ثبت عملکرد حرکت‌ها"),
    )
    sets_min = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name=_("حداقل ست"))
    sets_max = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name=_("حداکثر ست"))
    reps_min = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name=_("حداقل تکرار"))
    reps_max = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name=_("حداکثر تکرار"))
    target_load_kg = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True, verbose_name=_("بار هدف، کیلوگرم"))
    target_load_percent_1rm = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, verbose_name=_("درصد بار هدف از یک تکرار بیشینه"))
    target_rpe = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MinValueValidator(0), MaxValueValidator(10)], verbose_name=_("RPE هدف"))
    target_rir = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MinValueValidator(0), MaxValueValidator(10)], verbose_name=_("RIR هدف"))
    warmup_sets = models.JSONField(default=list, blank=True, verbose_name=_("ست‌های گرم‌کردن"))
    previous_performance_snapshot = models.JSONField(default=dict, blank=True, verbose_name=_("خلاصه عملکرد قبلی"))
    weekly_set_targets = models.JSONField(default=dict, blank=True, verbose_name=_("تعداد ست در هر هفته چرخه"))
    rest_seconds_min = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name=_("حداقل استراحت، ثانیه"))
    rest_seconds_max = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name=_("حداکثر استراحت، ثانیه"))
    cardio_duration_seconds = models.PositiveIntegerField(null=True, blank=True, verbose_name=_("مدت هوازی، ثانیه"))
    cardio_interval_count = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name=_("تعداد تناوب هوازی"))
    cardio_work_seconds = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name=_("مدت بخش کاری، ثانیه"))
    cardio_recovery_seconds = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name=_("مدت بازیابی، ثانیه"))
    target_heart_rate_min = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name=_("حداقل ضربان هدف"))
    target_heart_rate_max = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name=_("حداکثر ضربان هدف"))
    selection_reason = models.TextField(blank=True, verbose_name=_("دلیل انتخاب حرکت"))
    selection_score = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MaxValueValidator(100)], verbose_name=_("امتیاز سازگاری"))

    class Meta:
        ordering = ["order", "id"]
        verbose_name = "حرکت برنامه بدنسازی"
        verbose_name_plural = "حرکت‌های برنامه بدنسازی"
        indexes = [
            models.Index(fields=["day", "order"]),
            models.Index(fields=["exercise"]),
            models.Index(fields=["superset_exercise"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(exercise__isnull=False, cardio_exercise__isnull=True)
                    | models.Q(exercise__isnull=True, cardio_exercise__isnull=False)
                ),
                name="workout_program_exercise_one_movement",
            ),
            models.CheckConstraint(
                check=models.Q(sets_min__isnull=True) | models.Q(sets_max__isnull=True) | models.Q(sets_max__gte=models.F("sets_min")),
                name="workout_program_sets_range_valid",
            ),
            models.CheckConstraint(
                check=models.Q(reps_min__isnull=True) | models.Q(reps_max__isnull=True) | models.Q(reps_max__gte=models.F("reps_min")),
                name="workout_program_reps_range_valid",
            ),
            models.CheckConstraint(
                check=models.Q(rest_seconds_min__isnull=True) | models.Q(rest_seconds_max__isnull=True) | models.Q(rest_seconds_max__gte=models.F("rest_seconds_min")),
                name="workout_program_rest_range_valid",
            ),
            models.CheckConstraint(
                check=models.Q(target_load_kg__isnull=True) | models.Q(target_load_kg__gte=0),
                name="workout_program_load_nonnegative",
            ),
            models.CheckConstraint(
                check=models.Q(target_load_percent_1rm__isnull=True) | models.Q(target_load_percent_1rm__lte=150),
                name="workout_program_intensity_percent_max_150",
            ),
            models.CheckConstraint(
                check=models.Q(target_heart_rate_min__isnull=True) | models.Q(target_heart_rate_max__isnull=True) | models.Q(target_heart_rate_max__gte=models.F("target_heart_rate_min")),
                name="workout_program_hr_range_valid",
            ),
        ]

    def clean(self):
        if bool(self.exercise_id) == bool(self.cardio_exercise_id):
            raise ValidationError(_("برای حرکت اصلی دقیقاً یک حرکت بدنسازی یا هوازی انتخاب کنید."))
        if self.superset_exercise_id and self.exercise_id == self.superset_exercise_id:
            raise ValidationError({"superset_exercise": _("حرکت دوم سوپرست باید با حرکت اصلی متفاوت باشد.")})
        if self.third_exercise_id and self.exercise_id == self.third_exercise_id:
            raise ValidationError({"third_exercise": _("حرکت سوم باید با حرکت اصلی متفاوت باشد.")})
        if (
            self.third_exercise_id
            and self.superset_exercise_id
            and self.superset_exercise_id == self.third_exercise_id
        ):
            raise ValidationError({"third_exercise": _("حرکت سوم باید با حرکت دوم متفاوت باشد.")})

    def save(self, *args, **kwargs):
        self.goal = canonical_goal_key(self.goal)
        super().save(*args, **kwargs)

    @property
    def is_superset(self):
        return bool(self.superset_exercise_id)

    @property
    def movement(self):
        return self.cardio_exercise or self.exercise

    def __str__(self):
        movements = [self.movement.name]
        if self.superset_exercise_id:
            movements.append(self.superset_exercise.name)
        if self.third_exercise_id:
            movements.append(self.third_exercise.name)
        return " + ".join(movements)


class WorkoutProgramCorrective(models.Model):
    class Phase(models.TextChoices):
        WARMUP = "warmup", _("حین گرم کردن")
        COOLDOWN = "cooldown", _("حین سرد کردن")

    program = models.ForeignKey(
        WorkoutProgram,
        on_delete=models.CASCADE,
        related_name="corrective_items",
        verbose_name=_("برنامه"),
    )
    corrective_exercise = models.ForeignKey(
        CorrectiveExercise,
        on_delete=models.CASCADE,
        related_name="program_items",
        verbose_name=_("حرکت اصلاحی"),
    )
    phase = models.CharField(max_length=20, choices=Phase.choices, verbose_name=_("مرحله اجرا"))
    sets = models.CharField(max_length=40, blank=True, verbose_name=_("تعداد ست"))
    reps = models.CharField(max_length=60, blank=True, verbose_name=_("تکرار/مدت"))
    note = models.TextField(blank=True, verbose_name=_("نکته"))
    order = models.PositiveSmallIntegerField(default=1, verbose_name=_("ترتیب"))

    class Meta:
        ordering = ["phase", "order", "id"]
        verbose_name = "حرکت اصلاحی برنامه"
        verbose_name_plural = "حرکت‌های اصلاحی برنامه"
        indexes = [
            models.Index(fields=["program", "phase", "order"]),
            models.Index(fields=["corrective_exercise"]),
        ]

    def __str__(self):
        return self.corrective_exercise.name


class WorkoutProgramFeedback(models.Model):
    program = models.ForeignKey(
        WorkoutProgram,
        on_delete=models.CASCADE,
        related_name="difficulty_feedback",
        verbose_name=_("برنامه"),
    )
    day = models.ForeignKey(
        WorkoutProgramDay,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="difficulty_feedback",
        verbose_name=_("روز برنامه"),
    )
    difficulty = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(10)],
        verbose_name=_("سطح دشواری"),
    )
    submitted_at = models.DateTimeField(auto_now=True, verbose_name=_("زمان ثبت بازخورد"))

    class Meta:
        verbose_name = "بازخورد برنامه بدنسازی"
        verbose_name_plural = "بازخوردهای برنامه بدنسازی"
        ordering = ["-submitted_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["program", "day"],
                name="unique_workout_program_feedback_day",
            ),
        ]

    def __str__(self):
        return f"{self.program.title} - {self.difficulty}/10"


class ProgramTemplate(models.Model):
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="owned_program_templates")
    title = models.CharField(max_length=150)
    training_goal = models.CharField(max_length=24, choices=TrainingGoal.choices)
    difficulty = models.ForeignKey(ExerciseDifficultyLevel, on_delete=models.PROTECT, related_name="program_templates")
    days_per_week = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(7)])
    weeks = models.PositiveSmallIntegerField(default=4, validators=[MinValueValidator(1), MaxValueValidator(18)])
    payload = models.JSONField(default=dict, blank=True)
    is_shared_with_enabled_coaches = models.BooleanField(default=False)
    shared_with = models.ManyToManyField(User, blank=True, related_name="shared_program_templates")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["title", "pk"]
        constraints = [models.UniqueConstraint(fields=["owner", "title"], name="unique_program_template_owner_title")]

    def __str__(self):
        return self.title
