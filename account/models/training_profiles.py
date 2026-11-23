"""Athlete preferences and training volume targets."""

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from account.domain.exercise_goals import canonical_goal_key

from .identity import User
from .exercise_library import Muscle, Exercise


class TrainingGoal(models.TextChoices):
    GENERAL_FITNESS = "general_fitness", _("سلامت و آمادگی عمومی")
    STRENGTH = "strength", _("افزایش قدرت")
    HYPERTROPHY = "hypertrophy", _("افزایش حجم عضلانی")
    ENDURANCE = "endurance", _("استقامت")
    MOBILITY = "mobility", _("انعطاف و تحرک")
    BODY_COMPOSITION = "body_composition", _("ترکیب بدنی")
    SPORT_PERFORMANCE = "sport_performance", _("عملکرد ورزشی")


class AthleteTrainingProfile(models.Model):
    """Structured preferences used to explain and personalize training suggestions."""

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="training_profile",
        verbose_name=_("ورزشکار"),
    )
    primary_goal = models.CharField(
        max_length=80,
        default="general",
        verbose_name=_("هدف اصلی تمرین"),
    )
    target_date = models.DateField(null=True, blank=True, verbose_name=_("تاریخ هدف"))
    sessions_per_week = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(7)],
        verbose_name=_("جلسه در هفته"),
    )
    experience_years = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MaxValueValidator(100)],
        verbose_name=_("سابقه تمرین، سال"),
    )
    session_duration_minutes = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(15), MaxValueValidator(180)],
        verbose_name=_("حداکثر مدت هر جلسه، دقیقه"),
        help_text=_("اختیاری؛ زمان در دسترس برای هر جلسه را بین ۱۵ تا ۱۸۰ دقیقه وارد کنید."),
    )
    preferred_days = models.JSONField(default=list, blank=True, verbose_name=_("روزهای ترجیحی تمرین"))
    available_equipment = models.ManyToManyField(
        "ExerciseEquipmentType",
        blank=True,
        related_name="athlete_training_profiles",
        verbose_name=_("تجهیزات در دسترس"),
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("ترجیحات تمرینی ورزشکار")
        verbose_name_plural = _("ترجیحات تمرینی ورزشکاران")
        constraints = [
            models.CheckConstraint(
                check=models.Q(sessions_per_week__isnull=True)
                | models.Q(sessions_per_week__gte=1, sessions_per_week__lte=7),
                name="athlete_sessions_per_week_range",
            ),
        ]

    def save(self, *args, **kwargs):
        self.primary_goal = canonical_goal_key(self.primary_goal) or "general"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user.fullname} - {self.get_primary_goal_display()}"

    def get_primary_goal_display(self):
        from account.services.exercise_goal_service import ExerciseGoalService

        return ExerciseGoalService.label(self.primary_goal)


class MuscleVolumeLandmark(models.Model):
    class ExperienceBand(models.TextChoices):
        BEGINNER = "beginner", _("مبتدی")
        INTERMEDIATE = "intermediate", _("متوسط")
        EXPERIENCED = "experienced", _("باتجربه")
        UNSPECIFIED = "unspecified", _("نامشخص")

    muscle = models.ForeignKey(Muscle, on_delete=models.CASCADE, related_name="volume_landmarks")
    training_goal = models.CharField(max_length=24, choices=TrainingGoal.choices)
    experience_band = models.CharField(max_length=16, choices=ExperienceBand.choices)
    mev = models.PositiveSmallIntegerField(verbose_name=_("حداقل حجم مؤثر، ست در هفته"))
    mav = models.PositiveSmallIntegerField(verbose_name=_("حجم سازگار، ست در هفته"))
    mrv = models.PositiveSmallIntegerField(verbose_name=_("حداکثر حجم بازیافت‌پذیر، ست در هفته"))
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["muscle__name", "training_goal", "experience_band"]
        constraints = [
            models.UniqueConstraint(fields=["muscle", "training_goal", "experience_band"], name="unique_muscle_volume_landmark"),
            models.CheckConstraint(check=models.Q(mev__lte=models.F("mav")), name="muscle_volume_mev_lte_mav"),
            models.CheckConstraint(check=models.Q(mav__lte=models.F("mrv")), name="muscle_volume_mav_lte_mrv"),
        ]

    def __str__(self):
        return f"{self.muscle} · {self.get_training_goal_display()} · {self.get_experience_band_display()}"


class AthleteExercisePreference(models.Model):
    class Status(models.TextChoices):
        FAVORITE = "favorite", _("محبوب")
        AVOID = "avoid", _("اجتناب")
        PAIN = "pain", _("درد یا محدودیت")

    class Source(models.TextChoices):
        ATHLETE = "athlete", _("ثبت ورزشکار")
        COACH_EDIT = "coach_edit", _("جایگزینی مربی")
        ADMIN = "admin", _("ثبت ادمین")

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="exercise_preferences")
    exercise = models.ForeignKey(Exercise, on_delete=models.CASCADE, related_name="athlete_preferences")
    status = models.CharField(max_length=12, choices=Status.choices)
    source = models.CharField(max_length=12, choices=Source.choices, default=Source.ATHLETE)
    reason = models.CharField(max_length=500, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="recorded_exercise_preferences")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["status", "exercise__name"]
        constraints = [models.UniqueConstraint(fields=["user", "exercise"], name="unique_athlete_exercise_preference")]
