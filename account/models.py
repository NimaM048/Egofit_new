import datetime

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models.signals import pre_delete
from django.dispatch import receiver
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from account.domain.exercise_goals import canonical_goal_key
from account.utils import normalize_phone_number


VIDEO_FILE_EXTENSIONS = frozenset(
    {"mp4", "webm", "mov", "m4v", "ogg", "ogv", "avi", "mkv"}
)
IMAGE_FILE_EXTENSIONS = frozenset(
    {"jpg", "jpeg", "png", "gif", "webp", "bmp", "avif", "heic", "heif"}
)


def default_mood_reminder_weekdays():
    return [0, 2, 4]


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


class TrainingGoal(models.TextChoices):
    GENERAL_FITNESS = "general_fitness", _("سلامت و آمادگی عمومی")
    STRENGTH = "strength", _("افزایش قدرت")
    HYPERTROPHY = "hypertrophy", _("افزایش حجم عضلانی")
    ENDURANCE = "endurance", _("استقامت")
    MOBILITY = "mobility", _("انعطاف و تحرک")
    BODY_COMPOSITION = "body_composition", _("ترکیب بدنی")
    SPORT_PERFORMANCE = "sport_performance", _("عملکرد ورزشی")


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


def _is_video_file_name(name):
    suffix = str(name or "").rsplit(".", 1)
    return len(suffix) == 2 and suffix[1].lower() in VIDEO_FILE_EXTENSIONS


class UserManager(BaseUserManager):
    _NON_MODEL_FIELDS = frozenset({"is_staff", "is_superuser"})

    def _strip_non_model_fields(self, extra_fields: dict) -> dict:
        return {key: value for key, value in extra_fields.items() if key not in self._NON_MODEL_FIELDS}

    def create_user(self, fullname, phone, password=None, **extra_fields):
        if not fullname:
            raise ValueError("Users must have a fullname")
        if not phone:
            raise ValueError("Users must have a phone number")

        normalized_phone = normalize_phone_number(phone)
        if len(normalized_phone) != 11 or not normalized_phone.isdigit():
            raise ValueError("Users must have a valid phone number")

        extra_fields = self._strip_non_model_fields(extra_fields)
        user = self.model(fullname=fullname.strip(), phone=normalized_phone, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, fullname, phone, password=None, **extra_fields):
        extra_fields.setdefault("is_admin", True)
        extra_fields.setdefault("is_active", True)
        extra_fields = self._strip_non_model_fields(extra_fields)
        if password is None:
            raise ValueError("Superusers must have a password")

        normalized_phone = normalize_phone_number(phone)
        desired_fullname = fullname.strip()
        existing = self.filter(phone=normalized_phone).first()
        if existing is not None:
            if (
                desired_fullname != existing.fullname
                and self.filter(fullname=desired_fullname).exclude(pk=existing.pk).exists()
            ):
                raise ValueError("A user with that fullname already exists.")
            if desired_fullname != existing.fullname:
                existing.fullname = desired_fullname
            existing.is_admin = True
            existing.is_active = True
            existing.set_password(password)
            for key, value in extra_fields.items():
                if hasattr(existing, key):
                    setattr(existing, key, value)
            existing.save(using=self._db)
            return existing

        return self.create_user(fullname=fullname, phone=phone, password=password, **extra_fields)


class User(AbstractBaseUser):
    class BloodGroupChoices(models.TextChoices):
        O_POSITIVE = "O+", "O+"
        O_NEGATIVE = "O-", "O-"
        A_POSITIVE = "A+", "A+"
        A_NEGATIVE = "A-", "A-"
        B_POSITIVE = "B+", "B+"
        B_NEGATIVE = "B-", "B-"
        AB_POSITIVE = "AB+", "AB+"
        AB_NEGATIVE = "AB-", "AB-"

    class GenderChoices(models.TextChoices):
        MALE = "male", _("مرد")
        FEMALE = "female", _("زن")

    class ActivityLevelChoices(models.TextChoices):
        SEDENTARY = "sedentary", _("کم‌تحرک (کمتر از ۵۰۰۰ قدم در روز)")
        LOW_ACTIVE = "low_active", _("کم‌فعال (۵۰۰۰ تا ۷۵۰۰ قدم در روز)")
        MODERATE_ACTIVE = "moderate_active", _("نسبتاً فعال (۷۵۰۰ تا ۱۰۰۰۰ قدم در روز)")
        ACTIVE = "active", _("فعال (۱۰۰۰۰ تا ۱۲۵۰۰ قدم در روز)")
        HIGH_ACTIVE = "high_active", _("بسیار فعال (بیش از ۱۲۵۰۰ قدم در روز)")

    email = models.EmailField(
        verbose_name=_("آدرس ایمیل"),
        max_length=255,
        null=True,
        blank=True,
        unique=True,
    )
    biography = models.TextField(null=True, blank=True)
    first_name = models.CharField(max_length=60, blank=True, verbose_name=_("نام"))
    last_name = models.CharField(max_length=60, blank=True, verbose_name=_("نام خانوادگی"))
    fullname = models.CharField(max_length=50, verbose_name=_("نام کامل"), unique=True)
    is_active = models.BooleanField(default=True)
    phone = models.CharField(max_length=12, unique=True, verbose_name=_("شماره تلفن"), db_index=True)
    is_admin = models.BooleanField(default=False, verbose_name=_("ادمین"))
    is_coach = models.BooleanField(default=False, db_index=True, verbose_name=_("مربی"))
    can_use_automatic_programming = models.BooleanField(
        default=False,
        verbose_name=_("دسترسی به برنامه‌ریزی خودکار"),
    )
    birthday_date = models.IntegerField(null=True, blank=True)
    birth_date_jalali = models.CharField(max_length=10, null=True, blank=True, verbose_name=_("تاریخ تولد"))
    weight_kg = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True, verbose_name=_("وزن (کیلوگرم)"))
    height_cm = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True, verbose_name=_("قد (سانتی‌متر)"))
    blood_group = models.CharField(
        max_length=3,
        choices=BloodGroupChoices.choices,
        null=True,
        blank=True,
        verbose_name=_("گروه خونی"),
    )
    web_site = models.CharField(max_length=100, null=True, blank=True)
    github = models.CharField(max_length=100, null=True, blank=True)
    linkdin = models.CharField(max_length=100, null=True, blank=True)
    telegram = models.CharField(max_length=100, null=True, blank=True)
    profile_picture = models.FileField(
        upload_to="profile_pictures/",
        null=True,
        blank=True,
        verbose_name=_("عکس پروفایل"),
    )
    display_name = models.CharField(max_length=80, null=True, blank=True, verbose_name=_("نام نمایشی"))
    age = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name=_("سن"))
    gender = models.CharField(
        max_length=10,
        choices=GenderChoices.choices,
        null=True,
        blank=True,
        verbose_name=_("جنسیت"),
    )
    activity_level = models.CharField(
        max_length=20,
        choices=ActivityLevelChoices.choices,
        null=True,
        blank=True,
        verbose_name=_("سطح فعالیت"),
    )
    coach = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="athletes",
        limit_choices_to=models.Q(is_admin=True) | models.Q(is_coach=True),
        verbose_name=_("مربی"),
    )

    objects = UserManager()

    USERNAME_FIELD = "fullname"
    REQUIRED_FIELDS = ["phone"]

    class Meta:
        verbose_name = "کاربر"
        verbose_name_plural = "کاربران"
        indexes = [
            models.Index(fields=["phone"]),
            models.Index(fields=["fullname"]),
        ]

    def __str__(self):
        return self.fullname

    def has_perm(self, perm, obj=None):
        return bool(self.is_active and self.is_admin)

    def has_module_perms(self, app_label):
        return bool(self.is_active and self.is_admin)

    @property
    def is_staff(self):
        return bool(self.is_active and self.is_admin)

    class Role(models.TextChoices):
        ADMIN = "admin", _("ادمین")
        COACH = "coach", _("مربی")
        ATHLETE = "athlete", _("ورزشکار")

    @property
    def role(self) -> str:
        """Single role label derived from the existing capability flags."""
        if self.is_admin:
            return self.Role.ADMIN
        if self.is_coach:
            return self.Role.COACH
        return self.Role.ATHLETE

    @property
    def role_label(self) -> str:
        return dict(self.Role.choices).get(self.role, self.role)

    def set_role(self, role: str) -> None:
        """Assign one of the three portal roles, keeping the flags in sync."""
        if role not in self.Role.values:
            raise ValueError(f"Unknown role: {role!r}")
        self.is_admin = role == self.Role.ADMIN
        self.is_coach = role == self.Role.COACH


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


class Otp(models.Model):
    token = models.CharField(max_length=200, null=True, db_index=True)
    phone = models.CharField(max_length=11, db_index=True)
    code_hash = models.CharField(max_length=64, blank=True, default="", verbose_name=_("هش کد"))
    code_encrypted = models.CharField(max_length=255, blank=True, default="", verbose_name=_("کد رمزگذاری‌شده"))
    exprision_date = models.DateTimeField(auto_now_add=True)
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True, verbose_name=_("زمان انقضا"))
    attempts = models.PositiveSmallIntegerField(default=0, verbose_name=_("تعداد تلاش ناموفق"))
    locked_until = models.DateTimeField(null=True, blank=True, verbose_name=_("پایان قفل موقت"))
    ip_address = models.GenericIPAddressField(null=True, blank=True, verbose_name=_("آی‌پی درخواست"))
    user_agent = models.CharField(max_length=255, blank=True, default="", verbose_name=_("مرورگر درخواست"))

    def __str__(self):
        return self.phone

    class Meta:
        verbose_name = _("کد یکبار مصرف")
        verbose_name_plural = _("کدهای یکبار مصرف")
        indexes = [
            models.Index(fields=["token", "created_at"], name="account_otp_token_created_idx"),
            models.Index(fields=["phone", "created_at"]),
            models.Index(fields=["created_at"]),
        ]


class OtpAuditLog(models.Model):
    class Event(models.TextChoices):
        REQUESTED = "requested", _("درخواست کد")
        SENT = "sent", _("ارسال کد")
        VERIFIED = "verified", _("تأیید موفق")
        INVALID = "invalid", _("کد نادرست")
        LOCKED = "locked", _("قفل موقت")
        EXPIRED = "expired", _("کد منقضی")
        RATE_LIMITED = "rate_limited", _("محدودسازی نرخ")
        DELIVERY_FAILED = "delivery_failed", _("خطای ارسال")

    phone = models.CharField(max_length=11, blank=True, default="", db_index=True, verbose_name=_("شماره تلفن"))
    ip_address = models.GenericIPAddressField(null=True, blank=True, verbose_name=_("آی‌پی"))
    user_agent = models.CharField(max_length=255, blank=True, default="", verbose_name=_("مرورگر"))
    event = models.CharField(max_length=20, choices=Event.choices, db_index=True, verbose_name=_("رویداد"))
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name=_("زمان"))

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = _("رخداد ممیزی کد یکبار مصرف")
        verbose_name_plural = _("رخدادهای ممیزی کد یکبار مصرف")
        indexes = [models.Index(fields=["ip_address", "created_at"], name="acct_otp_audit_ip_cr_idx")]

    def __str__(self):
        return f"{self.get_event_display()} - {self.phone or '—'}"


class UserSession(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    session_key = models.CharField(max_length=255, db_index=True)
    ip_address = models.GenericIPAddressField()
    user_agent = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(null=True, blank=True, verbose_name=_("آخرین فعالیت"))
    device_type = models.CharField(max_length=20, blank=True, default="", verbose_name=_("نوع دستگاه"))
    browser = models.CharField(max_length=60, blank=True, default="", verbose_name=_("مرورگر"))
    os = models.CharField(max_length=60, blank=True, default="", verbose_name=_("سیستم عامل"))
    is_bot = models.BooleanField(default=False, db_index=True, verbose_name=_("ربات"))
    is_blocked = models.BooleanField(default=False, db_index=True, verbose_name=_("مسدود شده"))
    login_count = models.PositiveIntegerField(default=1, verbose_name=_("تعداد ورود"))
    accept_language = models.CharField(max_length=120, blank=True, default="", verbose_name=_("زبان مرورگر"))
    referrer = models.CharField(max_length=500, blank=True, default="", verbose_name=_("ارجاع‌دهنده"))

    def __str__(self):
        return f"{self.user.fullname} - {self.ip_address}"

    def apply_user_agent_metadata(self) -> bool:
        """Populate device/browser/OS/bot fields from ``user_agent``."""
        if not self.user_agent:
            return False
        try:
            from user_agents import parse as parse_user_agent

            parsed = parse_user_agent(self.user_agent)
            if parsed.is_mobile:
                self.device_type = "mobile"
            elif parsed.is_tablet:
                self.device_type = "tablet"
            elif parsed.is_pc:
                self.device_type = "desktop"
            else:
                self.device_type = "other"
            self.browser = (parsed.browser.family or "")[:60]
            self.os = (parsed.os.family or "")[:60]
            self.is_bot = bool(parsed.is_bot)
        except Exception:
            return False
        return True

    def save(self, *args, **kwargs):
        self.apply_user_agent_metadata()
        super().save(*args, **kwargs)

    class Meta:
        verbose_name = _("نشست کاربر")
        verbose_name_plural = _("نشست‌های کاربران")
        indexes = [
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["session_key", "created_at"]),
            models.Index(fields=["ip_address", "created_at"]),
            models.Index(fields=["is_bot", "is_blocked"]),
        ]


class BlockedIP(models.Model):
    ip_address = models.GenericIPAddressField(unique=True, verbose_name=_("آی‌پی"))
    reason = models.CharField(max_length=255, blank=True, default="", verbose_name=_("دلیل مسدودسازی"))
    is_active = models.BooleanField(default=True, db_index=True, verbose_name=_("فعال"))
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True, verbose_name=_("پایان مسدودی"))

    class Meta:
        verbose_name = _("آی‌پی مسدود")
        verbose_name_plural = _("آی‌پی‌های مسدود")
        indexes = [models.Index(fields=["ip_address", "is_active"])]

    def __str__(self):
        return self.ip_address

    @property
    def is_currently_blocked(self) -> bool:
        if not self.is_active:
            return False
        return self.expires_at is None or self.expires_at > timezone.now()


class Notification(models.Model):
    class SmsStatus(models.TextChoices):
        NOT_REQUESTED = "not_requested", "درخواستی ثبت نشده"
        PENDING = "pending", "در انتظار ارسال"
        SENT = "sent", "ارسال شده"
        FAILED = "failed", "ناموفق"

    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    title = models.CharField(max_length=255, verbose_name="عنوان")
    message = models.TextField(verbose_name="پیام")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاریخ ایجاد")
    read = models.BooleanField(default=False, verbose_name="خوانده شده")
    is_global = models.BooleanField(default=False, verbose_name="اعلان عمومی")
    send_sms_to_all = models.BooleanField(default=False, verbose_name="ارسال پیامک به همه کاربران")
    sms_status = models.CharField(
        max_length=20,
        choices=SmsStatus.choices,
        default=SmsStatus.NOT_REQUESTED,
        verbose_name="وضعیت ارسال پیامک",
        db_index=True,
    )
    sms_sent_at = models.DateTimeField(null=True, blank=True, verbose_name="زمان ارسال پیامک")
    sms_recipients_count = models.PositiveIntegerField(default=0, verbose_name="تعداد دریافت‌کنندگان پیامک")
    sms_error = models.TextField(blank=True, verbose_name="شرح خطای پیامک")

    def __str__(self):
        return self.title

    class Meta:
        verbose_name = "اطلاعیه"
        verbose_name_plural = "اطلاعیه‌ها"
        indexes = [
            models.Index(fields=["created_at"]),
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["is_global", "created_at"]),
            models.Index(fields=["sms_status", "created_at"]),
        ]


class NotificationDismissal(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notification_dismissals")
    notification_key = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "notification_key"],
                name="unique_user_notification_dismissal",
            ),
        ]
        indexes = [models.Index(fields=["user", "created_at"], name="account_notif_user_created_idx")]


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


class ClientMedia(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="client_media")
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="uploaded_client_media")
    image = models.FileField(upload_to="client_media/images/", null=True, blank=True, verbose_name=_("تصویر"))
    video = models.FileField(upload_to="client_media/videos/", null=True, blank=True, verbose_name=_("ویدیو"))
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]
        verbose_name = "فایل کاربر"
        verbose_name_plural = "فایل‌های کاربر"


class ClientDocument(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="client_documents")
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="uploaded_client_documents")
    title = models.CharField(max_length=120, verbose_name=_("عنوان"))
    file = models.FileField(upload_to="client_media/documents/", verbose_name=_("فایل"))
    uploaded_at = models.DateTimeField(auto_now_add=True)
    legacy_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    requires_payment = models.BooleanField(default=False, verbose_name=_("نیازمند پرداخت"))
    price = models.PositiveIntegerField(default=0, verbose_name=_("هزینه (تومان)"))

    class Meta:
        ordering = ["-uploaded_at"]
        verbose_name = "برنامه/فایل کاربر"
        verbose_name_plural = "برنامه‌ها و فایل‌های کاربر"
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(requires_payment=False, price=0)
                    | models.Q(requires_payment=True, price__gt=0)
                ),
                name="client_document_access_price_consistent",
            ),
        ]

    def __str__(self):
        return self.title


class ClientDocumentPayment(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", _("در انتظار پرداخت")
        INITIATED = "initiated", _("پرداخت آغاز شده")
        PAID = "paid", _("پرداخت شده")
        FAILED = "failed", _("ناموفق")

    document = models.ForeignKey(ClientDocument, on_delete=models.CASCADE, related_name="payments")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="document_payments")
    responsible = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="responsible_document_payments",
        limit_choices_to=models.Q(is_admin=True) | models.Q(is_coach=True),
        verbose_name=_("مسئول درآمد"),
    )
    amount = models.PositiveIntegerField(verbose_name=_("مبلغ"))
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    authority = models.CharField(max_length=64, unique=True, null=True, blank=True)
    ref_id = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("پرداخت فایل کاربر")
        verbose_name_plural = _("پرداخت‌های فایل کاربران")
        indexes = [
            models.Index(fields=["status", "paid_at", "responsible"], name="acc_cdp_stat_paid_resp"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["document", "user"],
                condition=models.Q(status="initiated"),
                name="unique_active_document_payment",
            ),
        ]

    def __str__(self):
        return f"{self.user} - {self.document}"


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


class BirthdaySmsLog(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="birthday_sms_logs")
    sent_on = models.CharField(max_length=10, verbose_name=_("تاریخ ارسال (شمسی)"))

    class Meta:
        unique_together = (("user", "sent_on"),)
        verbose_name = "ارسال پیامک تبریک تولد"
        verbose_name_plural = "ارسال‌های پیامک تبریک تولد"

    def __str__(self):
        return f"{self.user.fullname} - {self.sent_on}"


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


@receiver(pre_delete, sender=Exercise)
def preserve_exercise_names_in_workout_records(sender, instance, **kwargs):
    WorkoutPerformanceRecord.objects.filter(exercise=instance).update(
        exercise_name_snapshot=instance.name
    )
    WorkoutBestRecord.objects.filter(exercise=instance).update(
        exercise_name_snapshot=instance.name
    )
