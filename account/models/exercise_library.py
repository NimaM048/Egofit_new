"""Movement catalog and prescription lookup tables."""

from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from account.domain.exercise_goals import canonical_goal_key

from .media import _is_video_file_name


class ExerciseTrainingMethod(models.TextChoices):
    NORMAL = "normal", _("معمولی")
    PYRAMID = "pyramid", "Pyramid"
    REVERSE_PYRAMID = "reverse_pyramid", "Reverse Pyramid"
    TWENTY_ONE_REPS = "21_reps", "21 Reps"
    DROP_SET = "drop_set", "Drop Set"
    LOW_TO_HIGH = "low_to_high", "Low To High"
    HIGH_TO_LOW = "high_to_low", "High To Low"


class CardioExerciseType(models.TextChoices):
    INTERVAL = "interval", _("اینتروال")
    CONTINUOUS = "continuous", _("تداومی")


class Muscle(models.Model):
    name = models.CharField(max_length=120, verbose_name=_("نام عضله"))
    name_en = models.CharField(max_length=120, blank=True, verbose_name=_("نام انگلیسی عضله"))
    origin = models.TextField(blank=True, verbose_name=_("مبدا"))
    insertion = models.TextField(blank=True, verbose_name=_("انتها"))
    nerve = models.CharField(max_length=120, blank=True, verbose_name=_("عصب"))
    function_note = models.TextField(blank=True, verbose_name=_("عملکرد"))
    image = models.ImageField(upload_to="muscles/", null=True, blank=True, verbose_name=_("تصویر"))

    class Meta:
        ordering = ["name"]
        verbose_name = "عضله"
        verbose_name_plural = "کتابخانه عضلات"

    def __str__(self):
        return self.name


class ExerciseBodyPart(models.Model):
    name = models.CharField(max_length=60, unique=True, verbose_name=_("بخش بدن"))
    name_en = models.CharField(max_length=80, blank=True, verbose_name=_("نام انگلیسی"))

    class Meta:
        ordering = ["name"]
        verbose_name = "بخش بدن"
        verbose_name_plural = "بخش‌های بدن"

    def __str__(self):
        return self.name


class ExerciseMovementType(models.Model):
    name = models.CharField(max_length=60, unique=True, verbose_name=_("نوع حرکت"))
    name_en = models.CharField(max_length=80, blank=True, verbose_name=_("نام انگلیسی"))

    class Meta:
        ordering = ["name"]
        verbose_name = "نوع حرکت"
        verbose_name_plural = "انواع حرکت"

    def __str__(self):
        return self.name


class ExerciseJointType(models.Model):
    name = models.CharField(max_length=60, unique=True, verbose_name=_("نوع مفصل"))
    name_en = models.CharField(max_length=80, blank=True, verbose_name=_("نام انگلیسی"))

    class Meta:
        ordering = ["name"]
        verbose_name = "نوع مفصل"
        verbose_name_plural = "انواع مفصل"

    def __str__(self):
        return self.name


class ExercisePowerType(models.Model):
    name = models.CharField(max_length=60, unique=True, verbose_name=_("نوع قدرت"))
    name_en = models.CharField(max_length=80, blank=True, verbose_name=_("نام انگلیسی"))

    class Meta:
        ordering = ["name"]
        verbose_name = "نوع قدرت"
        verbose_name_plural = "انواع قدرت"

    def __str__(self):
        return self.name


class ExerciseDifficultyLevel(models.Model):
    name = models.CharField(max_length=60, unique=True, verbose_name=_("سطح دشواری"))
    name_en = models.CharField(max_length=80, blank=True, verbose_name=_("نام انگلیسی"))

    class Meta:
        ordering = ["name"]
        verbose_name = "سطح دشواری"
        verbose_name_plural = "سطوح دشواری"

    def __str__(self):
        return self.name


class ExerciseEquipmentType(models.Model):
    name = models.CharField(max_length=60, unique=True, verbose_name=_("تجهیزات"))
    name_en = models.CharField(max_length=80, blank=True, verbose_name=_("نام انگلیسی"))
    load_increment_kg = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0.01)],
        verbose_name=_("گام افزایش وزنه، کیلوگرم"),
        help_text=_("کمترین افزایش قابل تهیه در باشگاه؛ برای تجهیزات بدون وزنه خالی بماند."),
    )

    class Meta:
        ordering = ["name"]
        verbose_name = "نوع تجهیزات"
        verbose_name_plural = "انواع تجهیزات"

    def __str__(self):
        return self.name


class ExerciseExecutionEquipmentType(models.Model):
    name = models.CharField(max_length=60, unique=True, verbose_name=_("نوع تجهیزات اجرایی"))
    name_en = models.CharField(max_length=80, blank=True, verbose_name=_("نام انگلیسی"))

    class Meta:
        ordering = ["name"]
        verbose_name = "نوع تجهیزات اجرایی"
        verbose_name_plural = "انواع تجهیزات اجرایی"

    def __str__(self):
        return self.name


class ExerciseSecondaryMovementType(models.Model):
    name = models.CharField(max_length=60, unique=True, verbose_name=_("نوع حرکت دوم"))
    name_en = models.CharField(max_length=80, blank=True, verbose_name=_("نام انگلیسی"))

    class Meta:
        ordering = ["name"]
        verbose_name = "نوع حرکت دوم"
        verbose_name_plural = "انواع حرکت دوم"

    def __str__(self):
        return self.name


class ExerciseAbnormalityType(models.Model):
    name = models.CharField(max_length=60, unique=True, verbose_name=_("ناهنجاری"))
    name_en = models.CharField(max_length=80, blank=True, verbose_name=_("نام انگلیسی"))

    class Meta:
        ordering = ["name"]
        verbose_name = "ناهنجاری"
        verbose_name_plural = "انواع ناهنجاری"

    def __str__(self):
        return self.name


class ExercisePressureType(models.Model):
    name = models.CharField(max_length=60, unique=True, verbose_name=_("نوع فشار"))
    name_en = models.CharField(max_length=80, blank=True, verbose_name=_("نام انگلیسی"))

    class Meta:
        ordering = ["name"]
        verbose_name = "نوع فشار"
        verbose_name_plural = "انواع فشار"

    def __str__(self):
        return self.name


class ExerciseSportType(models.Model):
    name = models.CharField(max_length=80, unique=True, verbose_name=_("نوع ورزش"))
    name_en = models.CharField(max_length=80, blank=True, verbose_name=_("نام انگلیسی"))

    class Meta:
        ordering = ["name"]
        verbose_name = "نوع ورزش"
        verbose_name_plural = "انواع ورزش"

    def __str__(self):
        return self.name


class ExerciseGoal(models.Model):
    name = models.CharField(max_length=80, unique=True, verbose_name=_("هدف"))
    name_en = models.CharField(max_length=80, unique=True, verbose_name=_("نام انگلیسی هدف"))

    class Meta:
        ordering = ["name"]
        verbose_name = "هدف برنامه"
        verbose_name_plural = "اهداف برنامه"

    def save(self, *args, **kwargs):
        self.name_en = canonical_goal_key(self.name_en)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class ExerciseSetType(models.Model):
    set_count = models.CharField(max_length=40, verbose_name=_("تعداد ست"))
    goal = models.CharField(max_length=80, blank=True, verbose_name=_("هدف"))
    method = models.CharField(
        max_length=20,
        choices=ExerciseTrainingMethod.choices,
        default=ExerciseTrainingMethod.NORMAL,
        db_index=True,
        verbose_name=_("روش تمرین"),
    )

    class Meta:
        ordering = ["goal", "set_count"]
        verbose_name = "نوع ست"
        verbose_name_plural = "انواع ست"

    def save(self, *args, **kwargs):
        self.goal = canonical_goal_key(self.goal)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.set_count} ({self.goal})" if self.goal else self.set_count


class ExerciseRepetitionType(models.Model):
    reps = models.CharField(max_length=40, verbose_name=_("تعداد تکرار"))
    goal = models.CharField(max_length=80, blank=True, verbose_name=_("هدف"))
    method = models.CharField(
        max_length=20,
        choices=ExerciseTrainingMethod.choices,
        default=ExerciseTrainingMethod.NORMAL,
        db_index=True,
        verbose_name=_("روش تمرین"),
    )

    class Meta:
        ordering = ["goal", "reps"]
        verbose_name = "نوع تکرار"
        verbose_name_plural = "انواع تکرار"

    def save(self, *args, **kwargs):
        self.goal = canonical_goal_key(self.goal)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.reps} ({self.goal})" if self.goal else self.reps


class ExerciseRestType(models.Model):
    rest_time = models.CharField(max_length=40, verbose_name=_("زمان استراحت"))
    goal = models.CharField(max_length=80, blank=True, verbose_name=_("هدف"))
    method = models.CharField(
        max_length=20,
        choices=ExerciseTrainingMethod.choices,
        default=ExerciseTrainingMethod.NORMAL,
        db_index=True,
        verbose_name=_("روش تمرین"),
    )

    class Meta:
        ordering = ["goal", "rest_time"]
        verbose_name = "نوع استراحت"
        verbose_name_plural = "انواع استراحت"

    def save(self, *args, **kwargs):
        self.goal = canonical_goal_key(self.goal)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.rest_time} ({self.goal})" if self.goal else self.rest_time


class Exercise(models.Model):
    name = models.CharField(max_length=120, verbose_name=_("نام حرکت"))
    name_en = models.CharField(max_length=160, blank=True, verbose_name=_("نام انگلیسی حرکت"))
    primary_muscle = models.ForeignKey(Muscle, on_delete=models.PROTECT, related_name="primary_exercises", verbose_name=_("عضله اصلی"))
    secondary_muscle = models.ForeignKey(Muscle, on_delete=models.SET_NULL, null=True, blank=True, related_name="secondary_exercises", verbose_name=_("عضله فرعی"))
    body_part = models.ForeignKey(ExerciseBodyPart, on_delete=models.PROTECT, related_name="exercises", verbose_name=_("بخش بدن"))
    movement_type = models.ForeignKey(ExerciseMovementType, on_delete=models.PROTECT, related_name="exercises", verbose_name=_("نوع حرکت"))
    joint_type = models.ForeignKey(ExerciseJointType, on_delete=models.PROTECT, related_name="exercises", verbose_name=_("نوع مفصل"))
    power_type = models.ForeignKey(ExercisePowerType, on_delete=models.PROTECT, related_name="exercises", verbose_name=_("نوع قدرت"))
    difficulty_level = models.ForeignKey(ExerciseDifficultyLevel, on_delete=models.PROTECT, related_name="exercises", verbose_name=_("سطح دشواری"))
    equipment_type = models.ForeignKey(ExerciseEquipmentType, on_delete=models.PROTECT, related_name="exercises", verbose_name=_("تجهیزات"))
    required_equipment_types = models.ManyToManyField(
        ExerciseEquipmentType,
        blank=True,
        related_name="exercises_requiring_equipment",
        verbose_name=_("تجهیزات لازم"),
        help_text=_("در صورت خالی بودن، مقدار قدیمی فیلد تجهیزات استفاده می‌شود."),
    )
    excluded_abnormalities = models.ManyToManyField(
        ExerciseAbnormalityType,
        blank=True,
        related_name="excluded_exercises",
        verbose_name=_("محدودیت‌های نیازمند بررسی برای این حرکت"),
    )
    execution_equipment_type = models.ForeignKey(ExerciseExecutionEquipmentType, on_delete=models.PROTECT, null=True, blank=True, related_name="exercises", verbose_name=_("نوع تجهیزات اجرایی"))
    secondary_movement_type = models.ForeignKey(ExerciseSecondaryMovementType, on_delete=models.PROTECT, null=True, blank=True, related_name="exercises", verbose_name=_("نوع حرکت دوم"))
    pressure_type = models.ForeignKey(ExercisePressureType, on_delete=models.PROTECT, null=True, blank=True, related_name="exercises", verbose_name=_("نوع فشار"))
    description = models.TextField(blank=True, verbose_name=_("توضیحات حرکت"))
    media = models.FileField(upload_to="exercises/", null=True, blank=True, verbose_name=_("تصویر یا ویدیو"))
    video_1 = models.FileField(upload_to="exercises/videos/", null=True, blank=True, verbose_name=_("ویدیوی ۱"))
    video_2 = models.FileField(upload_to="exercises/videos/", null=True, blank=True, verbose_name=_("ویدیوی ۲"))
    video_3 = models.FileField(upload_to="exercises/videos/", null=True, blank=True, verbose_name=_("ویدیوی ۳"))

    class Meta:
        ordering = ["name"]
        verbose_name = "حرکت تمرینی"
        verbose_name_plural = "کتابخانه حرکات تمرینی"
        indexes = [
            models.Index(fields=["primary_muscle"]),
        ]

    def __str__(self):
        return self.name

    @property
    def video_files(self):
        files = []
        if self.media and _is_video_file_name(self.media.name):
            files.append(self.media)
        files.extend(
            file
            for file in (self.video_1, self.video_2, self.video_3)
            if file
        )
        return files

    @property
    def media_preview(self):
        return next(iter(self.video_files), None) or self.media

    @property
    def image_media(self):
        if self.media and not _is_video_file_name(self.media.name):
            return self.media
        return None


class CorrectiveExercise(models.Model):
    name = models.CharField(max_length=120, verbose_name=_("نام حرکت"))
    equipment = models.ForeignKey(ExerciseEquipmentType, on_delete=models.PROTECT, null=True, blank=True, related_name="corrective_exercises", verbose_name=_("تجهیزات مورد نیاز"))
    abnormality_type = models.ForeignKey(ExerciseAbnormalityType, on_delete=models.PROTECT, null=True, blank=True, related_name="corrective_exercises", verbose_name=_("نوع ناهنجاری"))
    media = models.FileField(upload_to="corrective_exercises/", null=True, blank=True, verbose_name=_("تصویر یا ویدیو"))
    video_1 = models.FileField(upload_to="corrective_exercises/videos/", null=True, blank=True, verbose_name=_("ویدیوی ۱"))
    video_2 = models.FileField(upload_to="corrective_exercises/videos/", null=True, blank=True, verbose_name=_("ویدیوی ۲"))
    video_3 = models.FileField(upload_to="corrective_exercises/videos/", null=True, blank=True, verbose_name=_("ویدیوی ۳"))
    description = models.TextField(blank=True, verbose_name=_("توضیحات"))

    class Meta:
        ordering = ["name"]
        verbose_name = "حرکت اصلاحی"
        verbose_name_plural = "کتابخانه حرکات اصلاحی"

    def __str__(self):
        return self.name

    @property
    def video_files(self):
        files = []
        if self.media and _is_video_file_name(self.media.name):
            files.append(self.media)
        files.extend(
            file
            for file in (self.video_1, self.video_2, self.video_3)
            if file
        )
        return files

    @property
    def media_preview(self):
        return next(iter(self.video_files), None) or self.media

    @property
    def image_media(self):
        if self.media and not _is_video_file_name(self.media.name):
            return self.media
        return None


class CardioExercise(models.Model):
    name = models.CharField(max_length=120, verbose_name=_("نام حرکت"))
    name_en = models.CharField(max_length=160, blank=True, verbose_name=_("نام انگلیسی حرکت"))
    performance_type = models.CharField(
        max_length=20,
        choices=CardioExerciseType.choices,
        verbose_name=_("نوع انجام"),
    )
    required_equipment_types = models.ManyToManyField(
        ExerciseEquipmentType,
        blank=True,
        related_name="cardio_exercises_requiring_equipment",
        verbose_name=_("تجهیزات لازم"),
    )
    image = models.ImageField(
        upload_to="cardio_exercises/",
        null=True,
        blank=True,
        verbose_name=_("تصویر حرکت"),
    )

    class Meta:
        ordering = ["name"]
        verbose_name = "حرکت هوازی"
        verbose_name_plural = "کتابخانه تمرینات هوازی"

    def __str__(self):
        return self.name
