"""User identities, authentication records, and account security."""

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from account.utils import normalize_phone_number


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


class BirthdaySmsLog(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="birthday_sms_logs")
    sent_on = models.CharField(max_length=10, verbose_name=_("تاریخ ارسال (شمسی)"))

    class Meta:
        unique_together = (("user", "sent_on"),)
        verbose_name = "ارسال پیامک تبریک تولد"
        verbose_name_plural = "ارسال‌های پیامک تبریک تولد"

    def __str__(self):
        return f"{self.user.fullname} - {self.sent_on}"
