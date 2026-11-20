from django import forms
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import ReadOnlyPasswordHashField
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, Sum
from django.http import HttpResponse
from django.utils.html import format_html
from django.urls import path
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from datetime import timedelta

from account.constants import GLOBAL_NOTIFICATION_CACHE_KEY
from account.admin_forms import CorrectiveExerciseForm
from account.models import (
    BlockedIP,
    CardioExercise,
    BodyCircumferenceMeasurement,
    CaliperMeasurement,
    ClientDocument,
    ClientDocumentPayment,
    ClientMedia,
    CoachApplication,
    CoachRequest,
    CorrectiveExercise,
    Exercise,
    ExerciseSuggestion,
    ExerciseBodyPart,
    ExerciseDifficultyLevel,
    ExerciseEquipmentType,
    ExerciseGoal,
    ExerciseJointType,
    ExerciseMovementType,
    ExercisePowerType,
    Muscle,
    Notification,
    Otp,
    OtpAuditLog,
    User,
    UserSession,
    WorkoutPerformanceRecord,
    WorkoutBestRecord,
    WorkoutProgram,
    WorkoutProgramPayment,
    WorkoutProgramCorrective,
    WorkoutProgramDay,
    WorkoutProgramExercise,
    WorkoutProgramFeedback,
)
from account.otp_security import decrypt_otp_code
from account.services.notification_service import NotificationService
from account.security_middleware import invalidate_blocked_ip_cache
from account.tasks.dispatch import dispatch_task
from account.tasks.sms_tasks import send_notification_sms_broadcast as send_notification_sms_broadcast_task
from account.validators import validate_strong_password


class UserCreationForm(forms.ModelForm):
    password1 = forms.CharField(label=_("گذرواژه"), widget=forms.PasswordInput, required=False)
    password2 = forms.CharField(label=_("تایید گذرواژه"), widget=forms.PasswordInput, required=False)

    class Meta:
        model = User
        fields = ["fullname", "phone", "email", "is_active", "is_admin"]

    def clean_password2(self):
        password1 = self.cleaned_data.get("password1")
        password2 = self.cleaned_data.get("password2")
        if password1 or password2:
            if password1 != password2:
                raise ValidationError(_("رمز عبور و تکرار آن یکسان نیست."))
            validate_strong_password(password1)
        return password2

    def save(self, commit=True):
        user = super().save(commit=False)
        password = self.cleaned_data.get("password1")
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        if commit:
            user.save()
        return user


class UserChangeForm(forms.ModelForm):
    password = ReadOnlyPasswordHashField(
        label=_("رمز عبور"),
    )
    password_status = forms.CharField(
        label=_("وضعیت رمز عبور"),
        required=False,
        disabled=True,
        help_text=_("رمز ذخیره‌شده قابل مشاهده نیست؛ فقط می‌توانید رمز جدید تنظیم کنید."),
    )

    class Meta:
        model = User
        fields = [
            "password",
            "fullname",
            "phone",
            "email",
            "display_name",
            "first_name",
            "last_name",
            "is_active",
            "is_admin",
            "coach",
            "birth_date_jalali",
            "weight_kg",
            "height_cm",
            "blood_group",
            "gender",
            "activity_level",
            "biography",
            "telegram",
            "github",
            "linkdin",
            "web_site",
            "profile_picture",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            if self.instance.has_usable_password():
                self.fields["password_status"].initial = _("تنظیم شده (ورود با نام کاربری و رمز)")
            else:
                self.fields["password_status"].initial = _("تنظیم نشده (فقط ورود با OTP)")

@admin.action(description=_("فعال‌سازی کاربران انتخاب‌شده"))
def activate_users(modeladmin, request, queryset):
    updated = queryset.update(is_active=True)
    modeladmin.message_user(request, _("%(count)s کاربر فعال شد.") % {"count": updated})


@admin.action(description=_("غیرفعال‌سازی کاربران انتخاب‌شده"))
def deactivate_users(modeladmin, request, queryset):
    updated = queryset.update(is_active=False)
    modeladmin.message_user(request, _("%(count)s کاربر غیرفعال شد.") % {"count": updated})


@admin.action(description=_("تبدیل به ادمین"))
def promote_to_admin(modeladmin, request, queryset):
    updated = queryset.update(is_admin=True)
    modeladmin.message_user(request, _("%(count)s کاربر به ادمین تبدیل شد.") % {"count": updated})


@admin.action(description=_("حذف دسترسی ادمین"))
def demote_from_admin(modeladmin, request, queryset):
    if queryset.filter(is_admin=True).count() == User.objects.filter(is_admin=True).count():
        modeladmin.message_user(request, _("حداقل یک ادمین باید در سیستم باقی بماند."), level=messages.ERROR)
        return
    updated = queryset.update(is_admin=False)
    modeladmin.message_user(request, _("%(count)s کاربر از حالت ادمین خارج شد.") % {"count": updated})


@admin.action(description=_("تبدیل به مربی"))
def promote_to_coach(modeladmin, request, queryset):
    updated = queryset.filter(is_admin=False).update(is_coach=True)
    modeladmin.message_user(request, _("%(count)s کاربر به مربی تبدیل شد.") % {"count": updated})


@admin.action(description=_("حذف دسترسی مربی"))
def demote_from_coach(modeladmin, request, queryset):
    coaches = list(queryset.filter(is_admin=False, is_coach=True))
    updated = queryset.filter(is_admin=False, is_coach=True).update(is_coach=False)
    for coach in coaches:
        # Free athletes assigned to a coach who no longer has coach access.
        User.objects.filter(coach=coach).update(coach=None)
    modeladmin.message_user(request, _("%(count)s کاربر از حالت مربی خارج شد.") % {"count": updated})


class UserAdmin(BaseUserAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    change_form_template = "admin/account/user/change_form.html"
    change_list_template = "admin/account/user/change_list.html"
    list_display = ["fullname", "phone", "email", "password_status_display", "is_admin", "is_coach", "is_active", "coach"]
    list_select_related = ("coach",)
    list_filter = ["is_admin", "is_coach", "is_active", "gender", "activity_level"]
    search_fields = ["email", "fullname", "phone", "display_name"]
    ordering = ["fullname"]
    filter_horizontal = ()
    actions = [
        activate_users,
        deactivate_users,
        promote_to_admin,
        demote_from_admin,
        promote_to_coach,
        demote_from_coach,
    ]
    readonly_fields = ["last_login"]

    fieldsets = [
        (
            _("ورود و امنیت"),
            {
                "fields": [
                    "password",
                    "password_status",
                    "last_login",
                ]
            },
        ),
        (
            _("اطلاعات اصلی"),
            {
                "fields": [
                    "fullname",
                    "phone",
                    "email",
                    "display_name",
                    "first_name",
                    "last_name",
                    "profile_picture",
                ]
            },
        ),
        (
            _("پروفایل سلامت"),
            {
                "fields": [
                    "birth_date_jalali",
                    "weight_kg",
                    "height_cm",
                    "blood_group",
                    "gender",
                    "activity_level",
                    "coach",
                ]
            },
        ),
        (
            _("شبکه‌های اجتماعی"),
            {"fields": ["biography", "telegram", "github", "linkdin", "web_site"]},
        ),
        (_("دسترسی‌ها"), {"fields": ["is_active", "is_admin", "is_coach"]}),
    ]
    add_fieldsets = [
        (
            None,
            {
                "classes": ["wide"],
                "fields": [
                    "fullname",
                    "phone",
                    "email",
                    "password1",
                    "password2",
                    "is_active",
                    "is_admin",
                    "is_coach",
                ],
            },
        ),
    ]

    @admin.display(boolean=True, description=_("رمز دارد"))
    def password_status_display(self, obj):
        return obj.has_usable_password()

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "download-phone-backup/",
                self.admin_site.admin_view(self.download_phone_backup),
                name="account_user_phone_backup",
            ),
        ]
        return custom_urls + urls

    def download_phone_backup(self, request):
        timestamp = timezone.localtime(timezone.now())
        filename = f"user-phone-backup-{timestamp:%Y%m%d-%H%M%S}.txt"
        phone_numbers = list(
            User.objects.order_by("fullname").values_list("phone", flat=True)
        )
        lines = [
            f"generated_at: {timestamp:%Y-%m-%d %H:%M:%S %Z}",
            f"total_numbers: {len(phone_numbers)}",
            "",
        ]
        lines.extend(phone_numbers)

        response = HttpResponse("\n".join(lines), content_type="text/plain; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


admin.site.register(User, UserAdmin)
admin.site.unregister(Group)


@admin.register(Otp)
class OtpAdmin(admin.ModelAdmin):
    change_list_template = "admin/account/otp/change_list.html"
    list_display = ("phone", "otp_code_display", "attempts", "locked_until", "created_at", "expires_at", "ip_address")
    fields = (
        "token",
        "phone",
        "otp_code_display",
        "exprision_date",
        "created_at",
        "expires_at",
        "attempts",
        "locked_until",
        "ip_address",
        "user_agent",
    )
    search_fields = ("phone", "token", "ip_address")
    list_filter = ("created_at", "expires_at", "locked_until")
    readonly_fields = (
        "token",
        "phone",
        "otp_code_display",
        "exprision_date",
        "created_at",
        "expires_at",
        "attempts",
        "locked_until",
        "ip_address",
        "user_agent",
    )
    ordering = ("-created_at",)

    @admin.display(description=_("کد"))
    def otp_code_display(self, obj):
        if not obj.code_encrypted:
            return _("کد این رکورد قابل بازیابی نیست")
        code = decrypt_otp_code(token=obj.token, encrypted_code=obj.code_encrypted)
        if not code:
            return _("کد قابل نمایش نیست")
        return format_html('<span dir="ltr" style="font-family:monospace">{}</span>', code)

    def has_add_permission(self, request):
        return False

    def changelist_view(self, request, extra_context=None):
        now = timezone.now()
        fifteen_minutes_ago = now - timedelta(minutes=15)
        one_hour_ago = now - timedelta(hours=1)
        request_events = (OtpAuditLog.Event.REQUESTED, OtpAuditLog.Event.RATE_LIMITED)
        recent_requests = OtpAuditLog.objects.filter(
            event__in=request_events,
            created_at__gte=fifteen_minutes_ago,
        )
        context = {
            "otp_requests_last_15m": recent_requests.count(),
            "otp_requests_last_hour": OtpAuditLog.objects.filter(
                event__in=request_events,
                created_at__gte=one_hour_ago,
            ).count(),
            "otp_failures_last_hour": OtpAuditLog.objects.filter(
                event__in=(
                    OtpAuditLog.Event.INVALID,
                    OtpAuditLog.Event.LOCKED,
                    OtpAuditLog.Event.RATE_LIMITED,
                ),
                created_at__gte=one_hour_ago,
            ).count(),
            "otp_top_request_ips": list(
                recent_requests.exclude(ip_address__isnull=True)
                .values("ip_address")
                .annotate(total=Count("id"))
                .order_by("-total")[:5]
            ),
            "otp_spike_threshold": 20,
        }
        if extra_context:
            context.update(extra_context)
        return super().changelist_view(request, extra_context=context)


@admin.register(OtpAuditLog)
class OtpAuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "event", "phone", "ip_address", "user_agent")
    list_filter = ("event", "created_at")
    search_fields = ("phone", "ip_address", "user_agent")
    readonly_fields = ("phone", "ip_address", "user_agent", "event", "created_at")
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class UserSessionAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "session_key",
        "ip_address",
        "device_type",
        "browser",
        "os",
        "accept_language",
        "is_bot",
        "is_blocked",
        "login_count",
        "created_at",
        "last_seen_at",
    )
    list_select_related = ("user",)
    search_fields = (
        "user__fullname",
        "user__phone",
        "session_key",
        "ip_address",
        "user_agent",
        "browser",
        "os",
        "referrer",
    )
    list_filter = ("is_bot", "is_blocked", "device_type", "browser", "os", "accept_language", "created_at")
    list_per_page = 50
    readonly_fields = (
        "user",
        "session_key",
        "ip_address",
        "user_agent",
        "created_at",
        "last_seen_at",
        "device_type",
        "browser",
        "os",
        "is_bot",
        "login_count",
        "accept_language",
        "referrer",
    )
    ordering = ("-created_at",)
    actions = ("mark_as_bot", "unmark_as_bot", "block_sessions", "unblock_sessions")

    @admin.action(description=_("علامت‌گذاری به عنوان ربات"))
    def mark_as_bot(self, request, queryset):
        updated = queryset.update(is_bot=True)
        self.message_user(request, _("%(count)s نشست به عنوان ربات علامت‌گذاری شد.") % {"count": updated})

    @admin.action(description=_("حذف علامت ربات"))
    def unmark_as_bot(self, request, queryset):
        updated = queryset.update(is_bot=False)
        self.message_user(request, _("%(count)s نشست از حالت ربات خارج شد.") % {"count": updated})

    @admin.action(description=_("مسدودسازی آی‌پی نشست‌های انتخاب‌شده"))
    def block_sessions(self, request, queryset):
        blocked = 0
        ip_addresses = list(queryset.values_list("ip_address", flat=True).distinct())
        for ip_address in ip_addresses:
            if not ip_address:
                continue
            BlockedIP.objects.update_or_create(
                ip_address=ip_address,
                defaults={
                    "is_active": True,
                    "expires_at": None,
                    "reason": "مسدودسازی از پنل مدیریت",
                },
            )
            invalidate_blocked_ip_cache(ip_address)
            blocked += 1
        UserSession.objects.filter(ip_address__in=ip_addresses).update(is_blocked=True)
        self.message_user(request, _("%(count)s آی‌پی مسدود شد.") % {"count": blocked})

    @admin.action(description=_("رفع مسدودی آی‌پی نشست‌های انتخاب‌شده"))
    def unblock_sessions(self, request, queryset):
        ips = list(queryset.values_list("ip_address", flat=True).distinct())
        BlockedIP.objects.filter(ip_address__in=ips).update(is_active=False)
        for ip_address in ips:
            invalidate_blocked_ip_cache(ip_address)
        updated = UserSession.objects.filter(ip_address__in=ips).update(is_blocked=False)
        self.message_user(request, _("%(count)s نشست رفع مسدودی شد.") % {"count": updated})


admin.site.register(UserSession, UserSessionAdmin)


@admin.register(BlockedIP)
class BlockedIPAdmin(admin.ModelAdmin):
    list_display = ("ip_address", "is_active", "reason", "created_at", "expires_at")
    list_filter = ("is_active", "created_at")
    search_fields = ("ip_address", "reason")
    actions = ("activate_blocks", "deactivate_blocks")

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        invalidate_blocked_ip_cache(obj.ip_address)
        UserSession.objects.filter(ip_address=obj.ip_address).update(
            is_blocked=obj.is_currently_blocked,
        )

    @admin.action(description=_("مسدودسازی آی‌پی‌های انتخاب‌شده"))
    def activate_blocks(self, request, queryset):
        ip_addresses = list(queryset.values_list("ip_address", flat=True))
        queryset.update(is_active=True, expires_at=None)
        UserSession.objects.filter(ip_address__in=ip_addresses).update(is_blocked=True)
        for ip_address in ip_addresses:
            invalidate_blocked_ip_cache(ip_address)
        self.message_user(request, _("%(count)s آی‌پی مسدود شد.") % {"count": len(ip_addresses)})

    @admin.action(description=_("رفع مسدودی آی‌پی‌های انتخاب‌شده"))
    def deactivate_blocks(self, request, queryset):
        ip_addresses = list(queryset.values_list("ip_address", flat=True))
        queryset.update(is_active=False)
        UserSession.objects.filter(ip_address__in=ip_addresses).update(is_blocked=False)
        for ip_address in ip_addresses:
            invalidate_blocked_ip_cache(ip_address)
        self.message_user(request, _("مسدودی %(count)s آی‌پی رفع شد.") % {"count": len(ip_addresses)})

    def delete_model(self, request, obj):
        ip_address = obj.ip_address
        super().delete_model(request, obj)
        invalidate_blocked_ip_cache(ip_address)
        UserSession.objects.filter(ip_address=ip_address).update(is_blocked=False)

    def delete_queryset(self, request, queryset):
        ip_addresses = list(queryset.values_list("ip_address", flat=True))
        super().delete_queryset(request, queryset)
        UserSession.objects.filter(ip_address__in=ip_addresses).update(is_blocked=False)
        for ip_address in ip_addresses:
            invalidate_blocked_ip_cache(ip_address)


@admin.register(BodyCircumferenceMeasurement)
class BodyCircumferenceMeasurementAdmin(admin.ModelAdmin):
    list_display = ("user", "recorded_at", "height_snapshot", "weight_snapshot")
    list_select_related = ("user",)
    search_fields = ("user__fullname", "user__phone")
    ordering = ("-recorded_at",)

    @admin.display(description=_("قد"))
    def height_snapshot(self, obj):
        return obj.user.height_cm or "-"

    @admin.display(description=_("وزن"))
    def weight_snapshot(self, obj):
        return obj.user.weight_kg or "-"


@admin.register(CaliperMeasurement)
class CaliperMeasurementAdmin(admin.ModelAdmin):
    list_display = ("user", "recorded_at", "abdominal_mm", "triceps_mm", "thigh_mm")
    list_select_related = ("user",)
    search_fields = ("user__fullname", "user__phone")
    ordering = ("-recorded_at",)


@admin.register(ClientMedia)
class ClientMediaAdmin(admin.ModelAdmin):
    list_display = ("user", "uploaded_by", "uploaded_at", "image", "video")
    list_select_related = ("user", "uploaded_by")
    search_fields = ("user__fullname", "user__phone", "uploaded_by__fullname")
    ordering = ("-uploaded_at",)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if not change:
            NotificationService.schedule_media_uploaded(obj)


class NotificationAdminForm(forms.ModelForm):
    class Meta:
        model = Notification
        fields = "__all__"

    def clean(self):
        cleaned_data = super().clean()
        send_sms_to_all = cleaned_data.get("send_sms_to_all")
        is_global = cleaned_data.get("is_global")
        user = cleaned_data.get("user")

        if send_sms_to_all and not is_global:
            self.add_error("send_sms_to_all", _("ارسال پیامک گروهی فقط برای اعلان عمومی مجاز است."))
        if is_global and user is not None:
            self.add_error("user", _("برای اعلان عمومی نباید کاربر مشخص انتخاب شود."))
        return cleaned_data


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    form = NotificationAdminForm
    list_display = ("title", "user", "is_global", "send_sms_to_all", "sms_status", "sms_recipients_count", "created_at", "read")
    list_select_related = ("user",)
    list_filter = ("is_global", "send_sms_to_all", "sms_status", "read", "created_at")
    search_fields = ("title", "message")
    readonly_fields = ("sms_status", "sms_sent_at", "sms_recipients_count", "sms_error", "created_at")
    ordering = ("-created_at",)

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "title",
                    "message",
                    "user",
                    "is_global",
                    "read",
                )
            },
        ),
        (
            _("ارسال پیامک"),
            {
                "fields": (
                    "send_sms_to_all",
                    "sms_status",
                    "sms_recipients_count",
                    "sms_sent_at",
                    "sms_error",
                )
            },
        ),
    )

    def save_model(self, request, obj, form, change):
        if obj.is_global:
            obj.user = None
        if obj.send_sms_to_all and obj.sms_status == Notification.SmsStatus.NOT_REQUESTED:
            obj.sms_status = Notification.SmsStatus.PENDING
        super().save_model(request, obj, form, change)

        if obj.is_global:
            cache.delete(f"{GLOBAL_NOTIFICATION_CACHE_KEY}:20")

        should_send_sms = obj.is_global and obj.send_sms_to_all and obj.sms_status in {
            Notification.SmsStatus.PENDING,
            Notification.SmsStatus.FAILED,
        }
        if not should_send_sms:
            return

        def _send_sms():
            try:
                dispatch_task(send_notification_sms_broadcast_task, obj.pk)
            except Exception as exc:
                self.message_user(
                    request,
                    _("اعلان ذخیره شد اما ارسال پیامک با خطا مواجه شد: %(error)s") % {"error": exc},
                    level=messages.ERROR,
                )
                return
            self.message_user(
                request,
                _("اعلان ذخیره شد و ارسال پیامک در صف قرار گرفت."),
                level=messages.SUCCESS,
            )

        transaction.on_commit(_send_sms)


@admin.register(ClientDocument)
class ClientDocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "uploaded_by", "uploaded_at", "requires_payment", "price")
    list_select_related = ("user", "uploaded_by")
    search_fields = ("title", "user__fullname", "user__phone")
    ordering = ("-uploaded_at",)
    list_filter = ("requires_payment",)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if not change:
            NotificationService.schedule_document_uploaded(obj)


@admin.register(CoachRequest)
class CoachRequestAdmin(admin.ModelAdmin):
    list_display = ("user", "sessions_per_week", "wants_diet", "wants_workout", "status", "created_at")
    list_select_related = ("user",)
    list_filter = ("status", "wants_diet", "wants_workout")
    search_fields = ("user__fullname", "user__phone")
    ordering = ("-created_at",)


class WorkoutProgramDayInline(admin.TabularInline):
    model = WorkoutProgramDay
    extra = 0
    fields = ("order", "name", "notes")
    ordering = ("order",)


class WorkoutProgramCorrectiveInline(admin.TabularInline):
    model = WorkoutProgramCorrective
    extra = 0
    fields = ("order", "phase", "corrective_exercise", "sets", "reps", "note")
    ordering = ("phase", "order")


class WorkoutProgramFeedbackInline(admin.StackedInline):
    model = WorkoutProgramFeedback
    extra = 0
    fields = ("day", "difficulty", "submitted_at")
    readonly_fields = ("submitted_at",)


@admin.register(WorkoutProgram)
class WorkoutProgramAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "user",
        "prescribed_by",
        "start_date",
        "end_date",
        "is_published",
        "requires_payment",
        "price",
        "difficulty_score",
        "updated_at",
    )
    list_select_related = ("user", "prescribed_by")
    list_filter = ("is_published", "requires_payment", "start_date", "end_date")
    search_fields = ("title", "user__fullname", "user__phone", "prescribed_by__fullname")
    ordering = ("-updated_at",)
    inlines = (WorkoutProgramDayInline, WorkoutProgramCorrectiveInline, WorkoutProgramFeedbackInline)

    def save_model(self, request, obj, form, change):
        was_published = False
        if change and obj.pk:
            was_published = bool(
                WorkoutProgram.objects.filter(pk=obj.pk).values_list("is_published", flat=True).first()
            )
        super().save_model(request, obj, form, change)
        if obj.is_published and (not change or not was_published):
            NotificationService.schedule_program_registered(obj)

    @admin.display(description=_("دشواری برنامه"))
    def difficulty_score(self, obj):
        feedback = obj.difficulty_feedback.order_by("-submitted_at").first()
        return f"{feedback.difficulty}/10" if feedback and feedback.difficulty is not None else "—"


class PaymentRevenueAdmin(admin.ModelAdmin):
    change_list_template = "admin/account/payment_changelist.html"

    def changelist_view(self, request, extra_context=None):
        response = super().changelist_view(request, extra_context=extra_context)
        if not hasattr(response, "context_data") or "cl" not in response.context_data:
            return response
        self._add_revenue_summary(response)
        return response

    @staticmethod
    def _add_revenue_summary(response):
        rows_by_responsible = {}
        payment_models = (
            (WorkoutProgramPayment, "program_revenue"),
            (ClientDocumentPayment, "document_revenue"),
        )
        for payment_model, revenue_field in payment_models:
            grouped = payment_model.objects.filter(status=payment_model.Status.PAID).values(
                "responsible_id", "responsible__fullname", "responsible__is_admin", "responsible__is_coach"
            ).annotate(total_amount=Sum("amount"), payment_count=Count("pk"))
            for item in grouped:
                responsible_id = item["responsible_id"]
                row = rows_by_responsible.setdefault(responsible_id, {
                    "name": item["responsible__fullname"] or "—",
                    "role": (
                        _("بدون انتساب") if responsible_id is None else
                        _("ادمین") if item["responsible__is_admin"] else
                        _("مربی") if item["responsible__is_coach"] else _("سایر")
                    ),
                    "program_revenue": 0,
                    "document_revenue": 0,
                    "total_amount": 0,
                    "payment_count": 0,
                })
                amount = item["total_amount"] or 0
                row[revenue_field] += amount
                row["total_amount"] += amount
                row["payment_count"] += item["payment_count"]
        rows = sorted(rows_by_responsible.values(), key=lambda item: item["name"])
        response.context_data.update({
            "revenue_by_responsible": rows,
            "revenue_total": sum(item["total_amount"] for item in rows),
            "revenue_payment_count": sum(item["payment_count"] for item in rows),
        })


@admin.register(WorkoutProgramPayment)
class WorkoutProgramPaymentAdmin(PaymentRevenueAdmin):
    list_display = ("program", "user", "amount", "responsible", "status", "authority", "created_at", "paid_at")
    list_editable = ("responsible",)
    list_select_related = ("program", "user", "responsible")
    list_filter = ("status", "responsible", "created_at", "paid_at")
    search_fields = ("program__title", "user__fullname", "user__phone", "authority", "ref_id")
    readonly_fields = ("created_at", "paid_at", "authority", "ref_id")
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        return False


@admin.register(ClientDocumentPayment)
class ClientDocumentPaymentAdmin(PaymentRevenueAdmin):
    list_display = ("document", "user", "amount", "responsible", "status", "created_at", "paid_at")
    list_select_related = ("document", "user", "responsible")
    list_editable = ("responsible",)
    list_filter = ("status", "responsible", "created_at", "paid_at")
    search_fields = ("document__title", "user__fullname", "user__phone", "authority", "ref_id")
    readonly_fields = ("document", "user", "amount", "status", "authority", "ref_id", "created_at", "paid_at")
    fields = ("document", "user", "amount", "responsible", "status", "authority", "ref_id", "created_at", "paid_at")

    def has_add_permission(self, request):
        return False


@admin.register(WorkoutProgramDay)
class WorkoutProgramDayAdmin(admin.ModelAdmin):
    list_display = ("name", "program", "order")
    list_select_related = ("program",)
    search_fields = ("name", "program__title", "program__user__fullname")
    ordering = ("program", "order")


@admin.register(WorkoutProgramExercise)
class WorkoutProgramExerciseAdmin(admin.ModelAdmin):
    list_display = ("movement", "superset_exercise", "third_exercise", "day", "sets", "reps", "rest", "order")
    list_select_related = ("day", "day__program", "exercise", "cardio_exercise", "superset_exercise", "third_exercise")
    search_fields = (
        "exercise__name",
        "cardio_exercise__name",
        "superset_exercise__name",
        "third_exercise__name",
        "day__program__title",
        "day__program__user__fullname",
    )
    ordering = ("day", "order")


@admin.register(CardioExercise)
class CardioExerciseAdmin(admin.ModelAdmin):
    list_display = ("name", "name_en", "performance_type")
    list_filter = ("performance_type",)
    search_fields = ("name", "name_en")


@admin.register(WorkoutProgramCorrective)
class WorkoutProgramCorrectiveAdmin(admin.ModelAdmin):
    list_display = ("corrective_exercise", "program", "phase", "sets", "reps", "order")
    list_select_related = ("program", "corrective_exercise")
    list_filter = ("phase",)
    search_fields = ("corrective_exercise__name", "program__title", "program__user__fullname")
    ordering = ("program", "phase", "order")


@admin.register(WorkoutPerformanceRecord)
class WorkoutPerformanceRecordAdmin(admin.ModelAdmin):
    list_display = (
        "exercise_label_display",
        "user",
        "repetitions",
        "mode",
        "set_number",
        "value",
        "recorded_on",
        "updated_at",
    )
    list_select_related = ("user", "exercise", "program")
    list_filter = ("mode", "recorded_on", "updated_at")
    search_fields = (
        "exercise__name",
        "exercise_name_snapshot",
        "user__fullname",
        "user__phone",
        "program__title",
    )
    ordering = ("-recorded_on", "-updated_at")

    @admin.display(description=_("حرکت"))
    def exercise_label_display(self, obj):
        return obj.exercise_label or "—"


@admin.register(WorkoutBestRecord)
class WorkoutBestRecordAdmin(admin.ModelAdmin):
    list_display = ("exercise_label_display", "user", "value", "recorded_at", "program")
    list_select_related = ("user", "exercise", "program")
    list_filter = ("recorded_at",)
    search_fields = ("exercise__name", "exercise_name_snapshot", "user__fullname", "user__phone", "program__title")
    ordering = ("-recorded_at",)

    @admin.display(description=_("حرکت"))
    def exercise_label_display(self, obj):
        return obj.exercise_label or "—"


@admin.register(Muscle)
class MuscleAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)
    ordering = ("name",)


@admin.register(Exercise)
class ExerciseAdmin(admin.ModelAdmin):
    list_display = ("name", "primary_muscle", "body_part", "difficulty_level")
    list_select_related = ("primary_muscle", "secondary_muscle", "body_part", "movement_type", "joint_type", "power_type", "difficulty_level", "equipment_type")
    list_filter = ("body_part", "movement_type", "difficulty_level", "equipment_type")
    search_fields = ("name", "primary_muscle__name")
    ordering = ("name",)


@admin.register(CorrectiveExercise)
class CorrectiveExerciseAdmin(admin.ModelAdmin):
    form = CorrectiveExerciseForm
    list_display = ("name", "abnormality_type", "equipment", "media", "video_count")
    list_select_related = ("abnormality_type", "equipment")
    list_filter = ("abnormality_type", "equipment")
    search_fields = ("name", "description", "abnormality_type__name", "equipment__name")
    ordering = ("name",)

    @admin.display(description=_("تعداد ویدیو"))
    def video_count(self, obj):
        return len(obj.video_files)


class _LookupModelAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)
    ordering = ("name",)


@admin.register(ExerciseGoal)
class ExerciseGoalAdmin(admin.ModelAdmin):
    list_display = ("name", "name_en")
    search_fields = ("name", "name_en")
    ordering = ("name",)


admin.site.register(ExerciseBodyPart, _LookupModelAdmin)
admin.site.register(ExerciseMovementType, _LookupModelAdmin)
admin.site.register(ExerciseJointType, _LookupModelAdmin)
admin.site.register(ExercisePowerType, _LookupModelAdmin)
admin.site.register(ExerciseDifficultyLevel, _LookupModelAdmin)
admin.site.register(ExerciseEquipmentType, _LookupModelAdmin)


@admin.register(CoachApplication)
class CoachApplicationAdmin(admin.ModelAdmin):
    list_display = ("full_name", "phone", "status", "created_at", "reviewed_by")
    list_filter = ("status",)
    search_fields = ("full_name", "phone", "user__fullname")
    readonly_fields = ("created_at", "updated_at", "reviewed_at")
    ordering = ("-created_at",)


@admin.register(ExerciseSuggestion)
class ExerciseSuggestionAdmin(admin.ModelAdmin):
    list_display = ("name_fa", "name_en", "kind", "status", "requested_by", "created_at")
    list_filter = ("status", "kind")
    search_fields = ("name_fa", "name_en", "requested_by__fullname")
    readonly_fields = ("created_at", "updated_at", "reviewed_at")
    ordering = ("-created_at",)
