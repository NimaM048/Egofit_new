"""Coach requests, applications, and exercise suggestions."""

from django.core.validators import MaxValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from .identity import User
from .media import IMAGE_FILE_EXTENSIONS, _is_video_file_name


class CoachRequest(models.Model):
    WORKOUT_TYPE_LABELS = {
        "gym_weights": _("تمرین با وزنه در باشگاه"),
        "home": _("تمرین در خانه"),
        "outdoor_running": _("دویدن"),
        "outdoor_swimming": _("شنا"),
        "outdoor_hiking": _("کوهنوردی"),
        "outdoor_cycling": _("دوچرخه‌سواری"),
    }

    class SessionsPerWeek(models.TextChoices):
        ONE = "1", _("۱ جلسه در هفته")
        TWO = "2", _("۲ جلسه در هفته")
        THREE = "3", _("۳ جلسه در هفته")
        FOUR = "4", _("۴ جلسه در هفته")
        FIVE = "5", _("۵ جلسه در هفته")
        SIX = "6", _("۶ جلسه در هفته")
        COACH_DISCRETION = "coach_discretion", _("هر چه مربی صلاح بداند")

    class Status(models.TextChoices):
        PENDING = "pending", _("در انتظار بررسی")
        HANDLED = "handled", _("بررسی شده")

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="coach_requests")
    sessions_per_week = models.CharField(max_length=20, choices=SessionsPerWeek.choices, verbose_name=_("تعداد جلسات در هفته"))
    wants_diet = models.BooleanField(default=False, verbose_name=_("درخواست برنامه غذایی"))
    wants_workout = models.BooleanField(default=False, verbose_name=_("درخواست برنامه تمرینی"))
    save_only = models.BooleanField(default=False, verbose_name=_("صرفا جهت ذخیره سازی در دیتابیس"))
    workout_types = models.JSONField(default=list, blank=True, verbose_name=_("انواع برنامه تمرینی"))
    available_equipment = models.ManyToManyField(
        "ExerciseEquipmentType",
        blank=True,
        related_name="coach_requests",
        verbose_name=_("تجهیزات در دسترس ورزشکار"),
    )
    training_experience_years = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MaxValueValidator(100)],
        verbose_name=_("سابقه تمرین (سال)"),
    )
    training_sports = models.TextField(blank=True, verbose_name=_("ورزش‌های انجام‌شده"))
    pain_notes = models.TextField(blank=True, verbose_name=_("درد یا آسیب"))
    illness_notes = models.TextField(blank=True, verbose_name=_("بیماری زمینه‌ای"))
    diet_restrictions = models.TextField(blank=True, verbose_name=_("محدودیت‌های غذایی"))
    # Athlete-submitted body data (field names mirror BodyCircumferenceMeasurement so the coach can copy 1:1).
    height_cm = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True, verbose_name=_("قد (سانتی‌متر)"))
    weight_kg = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True, verbose_name=_("وزن (کیلوگرم)"))
    wrist_cm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("دور مچ دست"))
    forearm_cm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("دور ساعد"))
    arm_rest_cm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("دور بازو آزاد"))
    arm_flexed_cm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("دور بازو منقبض"))
    chest_cm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("دور سینه"))
    shoulders_cm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("دور سرشانه"))
    waist_cm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("دور کمر"))
    abdomen_cm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("دور شکم"))
    hips_cm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("دور باسن"))
    thigh_cm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("دور ران"))
    calf_cm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("دور ساق پا"))
    thigh_left_cm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("دور ران (چپ)"))
    calf_left_cm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("دور ساق پا (چپ)"))
    chest_armpit_men_mm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("کالیپر سینه"))
    axilla_mm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("کالیپر زیربغل"))
    subscapular_mm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("کالیپر تحت کتفی"))
    abdominal_mm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("کالیپر شکم"))
    suprailiac_mm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("کالیپر سه‌تیغ خاصره"))
    chest_mm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("کالیپر کمر"))
    biceps_mm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("کالیپر جلوبازو"))
    triceps_mm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("کالیپر پشت بازو"))
    thigh_mm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("کالیپر جلوی ران"))
    calf_mm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name=_("کالیپر پشت ساق پا"))
    pushed_to_measurements = models.BooleanField(default=False, verbose_name=_("به اندازه‌گیری‌ها افزوده شد"))
    pushed_to_caliper = models.BooleanField(default=False, verbose_name=_("به کالیپر افزوده شد"))
    pushed_health_to_records = models.BooleanField(default=False, verbose_name=_("اطلاعات سلامت به سوابق افزوده شد"))
    pushed_attachments_to_files = models.BooleanField(default=False, verbose_name=_("پیوست‌ها به فایل‌های کاربر افزوده شد"))
    admin_read_at = models.DateTimeField(null=True, blank=True, verbose_name=_("زمان مشاهده توسط ادمین"))
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True, verbose_name=_("وضعیت"))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    handled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "درخواست از مربی"
        verbose_name_plural = "درخواست‌های از مربی"
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["user", "created_at"]),
        ]

    def __str__(self):
        return f"{self.user.fullname} - {self.get_status_display()}"

    @property
    def has_body_data(self) -> bool:
        return any(
            getattr(self, field) is not None
            for field in (
                "height_cm", "weight_kg", "wrist_cm", "forearm_cm", "arm_rest_cm",
                "arm_flexed_cm", "chest_cm", "shoulders_cm", "waist_cm", "abdomen_cm",
                "hips_cm", "thigh_cm", "calf_cm", "thigh_left_cm", "calf_left_cm",
            )
        )

    @property
    def has_caliper_data(self) -> bool:
        return any(
            getattr(self, field) is not None
            for field in (
                "chest_armpit_men_mm", "axilla_mm", "subscapular_mm", "abdominal_mm",
                "suprailiac_mm", "chest_mm", "biceps_mm", "triceps_mm", "thigh_mm", "calf_mm",
            )
        )

    @property
    def workout_type_labels(self):
        return [self.WORKOUT_TYPE_LABELS.get(value, value) for value in (self.workout_types or [])]


class CoachRequestAttachment(models.Model):
    request = models.ForeignKey(CoachRequest, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(upload_to="coach_requests/", verbose_name=_("فایل پیوست"))
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["uploaded_at"]
        verbose_name = "پیوست درخواست مربی"
        verbose_name_plural = "پیوست‌های درخواست مربی"

    def __str__(self):
        return self.file.name

    @property
    def is_video(self) -> bool:
        return _is_video_file_name(self.file.name)

    @property
    def is_image(self) -> bool:
        suffix = str(self.file.name or "").rsplit(".", 1)
        return len(suffix) == 2 and suffix[1].lower() in IMAGE_FILE_EXTENSIONS


class CoachApplication(models.Model):
    """An athlete's request to be promoted to a coach account."""

    class Status(models.TextChoices):
        PENDING = "pending", _("در انتظار بررسی")
        APPROVED = "approved", _("تایید شده")
        REJECTED = "rejected", _("رد شده")

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="coach_applications",
        verbose_name=_("کاربر"),
    )
    full_name = models.CharField(max_length=120, verbose_name=_("نام و نام خانوادگی"))
    phone = models.CharField(max_length=12, verbose_name=_("شماره تماس"))
    biography = models.TextField(verbose_name=_("بیوگرافی"))
    resume = models.FileField(
        upload_to="coach_applications/resumes/",
        null=True,
        blank=True,
        verbose_name=_("رزومه"),
    )
    photo = models.ImageField(
        upload_to="coach_applications/photos/",
        null=True,
        blank=True,
        verbose_name=_("عکس"),
    )
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
        verbose_name=_("وضعیت"),
    )
    rejection_reason = models.TextField(blank=True, verbose_name=_("دلیل رد درخواست"))
    reviewed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_coach_applications",
        verbose_name=_("بررسی‌کننده"),
    )
    reviewed_at = models.DateTimeField(null=True, blank=True, verbose_name=_("زمان بررسی"))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "درخواست مربی شدن"
        verbose_name_plural = "درخواست‌های مربی شدن"
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["user", "created_at"]),
        ]

    def __str__(self):
        return f"{self.full_name} - {self.get_status_display()}"


class ExerciseSuggestion(models.Model):
    """A coach's request for the admin to add a new exercise to the library."""

    class Kind(models.TextChoices):
        EXERCISE = "exercise", _("حرکت بدنسازی")
        CORRECTIVE = "corrective", _("حرکت اصلاحی")

    class Status(models.TextChoices):
        PENDING = "pending", _("در انتظار بررسی")
        APPROVED = "approved", _("تایید شده")
        REJECTED = "rejected", _("رد شده")

    requested_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="exercise_suggestions",
        verbose_name=_("درخواست‌دهنده"),
    )
    kind = models.CharField(
        max_length=12,
        choices=Kind.choices,
        default=Kind.EXERCISE,
        verbose_name=_("نوع حرکت"),
    )
    name_fa = models.CharField(max_length=120, verbose_name=_("نام فارسی"))
    name_en = models.CharField(max_length=160, verbose_name=_("نام انگلیسی"))
    goal = models.TextField(verbose_name=_("هدف حرکت"))
    image = models.ImageField(
        upload_to="exercise_suggestions/",
        null=True,
        blank=True,
        verbose_name=_("تصویر حرکت"),
    )
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
        verbose_name=_("وضعیت"),
    )
    rejection_reason = models.TextField(blank=True, verbose_name=_("دلیل رد"))
    reviewed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_exercise_suggestions",
        verbose_name=_("بررسی‌کننده"),
    )
    reviewed_at = models.DateTimeField(null=True, blank=True, verbose_name=_("زمان بررسی"))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "پیشنهاد حرکت"
        verbose_name_plural = "پیشنهادهای حرکت"
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["requested_by", "created_at"]),
        ]

    def __str__(self):
        return f"{self.name_fa} ({self.get_kind_display()})"
