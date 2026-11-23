"""In-app notifications and per-user dismissals."""

from django.db import models

from .identity import User


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
