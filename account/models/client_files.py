"""Athlete media and access to shared documents."""

from django.db import models
from django.utils.translation import gettext_lazy as _

from .identity import User


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
