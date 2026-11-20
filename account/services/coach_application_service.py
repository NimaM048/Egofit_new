from __future__ import annotations

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from account.models import CoachApplication
from account.services.notification_service import NotificationService


class CoachApplicationService:
    """Athlete -> coach promotion requests with an auditable review trail."""

    def __init__(self, *, notification_service: NotificationService | None = None):
        self.notification_service = notification_service or NotificationService()

    def get_latest_for_user(self, user):
        return user.coach_applications.order_by("-created_at").first()

    def get_pending_for_user(self, user):
        return user.coach_applications.filter(status=CoachApplication.Status.PENDING).first()

    def get_pending_queryset(self):
        return (
            CoachApplication.objects.filter(status=CoachApplication.Status.PENDING)
            .select_related("user")
            .order_by("created_at")
        )

    def get_all_queryset(self):
        return CoachApplication.objects.select_related("user", "reviewed_by").order_by("-created_at")

    def pending_count(self) -> int:
        return CoachApplication.objects.filter(status=CoachApplication.Status.PENDING).count()

    @transaction.atomic
    def submit(self, *, user, form) -> CoachApplication:
        """Create a new request, or refresh the athlete's pending one."""
        instance = form.save(commit=False)
        instance.user = user
        instance.status = CoachApplication.Status.PENDING
        instance.rejection_reason = ""
        instance.reviewed_by = None
        instance.reviewed_at = None

        pending = self.get_pending_for_user(user)
        if pending is None:
            instance.save()
            return instance

        # Keep a single pending request per athlete instead of stacking rows.
        for field in ("full_name", "phone", "biography", "resume", "photo"):
            value = getattr(instance, field)
            if field in {"resume", "photo"} and not value:
                continue
            setattr(pending, field, value)
        pending.save()
        return pending

    @transaction.atomic
    def approve(self, *, application: CoachApplication, actor) -> CoachApplication:
        if application.status == CoachApplication.Status.APPROVED:
            return application
        application.status = CoachApplication.Status.APPROVED
        application.rejection_reason = ""
        application.reviewed_by = actor
        application.reviewed_at = timezone.now()
        application.save(
            update_fields=["status", "rejection_reason", "reviewed_by", "reviewed_at", "updated_at"]
        )

        user = application.user
        if not user.is_admin:
            user.is_coach = True
            user.save(update_fields=["is_coach"])

        self.notification_service.notify_athlete_news(
            user,
            title=str(_("تایید درخواست مربی شدن")),
            message=str(
                _(
                    "درخواست مربی شدن شما تایید شد. از این پس به پنل مربی و "
                    "شاگردان اختصاصی خود دسترسی دارید."
                )
            ),
        )
        return application

    @transaction.atomic
    def reject(self, *, application: CoachApplication, actor, reason: str) -> CoachApplication:
        reason = (reason or "").strip()
        if not reason:
            raise ValueError("A rejection reason is required.")
        application.status = CoachApplication.Status.REJECTED
        application.rejection_reason = reason
        application.reviewed_by = actor
        application.reviewed_at = timezone.now()
        application.save(
            update_fields=["status", "rejection_reason", "reviewed_by", "reviewed_at", "updated_at"]
        )
        self.notification_service.notify_athlete_news(
            application.user,
            title=str(_("نتیجه درخواست مربی شدن")),
            message=str(_("درخواست مربی شدن شما رد شد. دلیل: {reason}")).format(reason=reason),
        )
        return application
