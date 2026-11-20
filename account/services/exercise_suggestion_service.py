from __future__ import annotations

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from account.models import ExerciseSuggestion
from account.services.notification_service import NotificationService


class ExerciseSuggestionService:
    """Coaches cannot edit the library; they submit suggestions for review."""

    def __init__(self, *, notification_service: NotificationService | None = None):
        self.notification_service = notification_service or NotificationService()

    def get_for_coach(self, coach):
        return ExerciseSuggestion.objects.filter(requested_by=coach).order_by("-created_at")

    def get_pending_queryset(self):
        return (
            ExerciseSuggestion.objects.filter(status=ExerciseSuggestion.Status.PENDING)
            .select_related("requested_by")
            .order_by("created_at")
        )

    def get_all_queryset(self):
        return ExerciseSuggestion.objects.select_related("requested_by", "reviewed_by").order_by(
            "-created_at"
        )

    def pending_count(self) -> int:
        return ExerciseSuggestion.objects.filter(status=ExerciseSuggestion.Status.PENDING).count()

    @transaction.atomic
    def submit(self, *, coach, form) -> ExerciseSuggestion:
        instance = form.save(commit=False)
        instance.requested_by = coach
        instance.status = ExerciseSuggestion.Status.PENDING
        instance.rejection_reason = ""
        instance.reviewed_by = None
        instance.reviewed_at = None
        instance.save()
        return instance

    @transaction.atomic
    def approve(self, *, suggestion: ExerciseSuggestion, actor) -> ExerciseSuggestion:
        suggestion.status = ExerciseSuggestion.Status.APPROVED
        suggestion.rejection_reason = ""
        suggestion.reviewed_by = actor
        suggestion.reviewed_at = timezone.now()
        suggestion.save(
            update_fields=["status", "rejection_reason", "reviewed_by", "reviewed_at", "updated_at"]
        )
        self.notification_service.notify_athlete_news(
            suggestion.requested_by,
            title=str(_("تایید پیشنهاد حرکت")),
            message=str(_("پیشنهاد حرکت «{name}» تایید شد و به کتابخانه اضافه می‌شود.")).format(
                name=suggestion.name_fa
            ),
        )
        return suggestion

    @transaction.atomic
    def reject(self, *, suggestion: ExerciseSuggestion, actor, reason: str) -> ExerciseSuggestion:
        reason = (reason or "").strip()
        if not reason:
            raise ValueError("A rejection reason is required.")
        suggestion.status = ExerciseSuggestion.Status.REJECTED
        suggestion.rejection_reason = reason
        suggestion.reviewed_by = actor
        suggestion.reviewed_at = timezone.now()
        suggestion.save(
            update_fields=["status", "rejection_reason", "reviewed_by", "reviewed_at", "updated_at"]
        )
        self.notification_service.notify_athlete_news(
            suggestion.requested_by,
            title=str(_("نتیجه پیشنهاد حرکت")),
            message=str(_("پیشنهاد حرکت «{name}» رد شد. دلیل: {reason}")).format(
                name=suggestion.name_fa, reason=reason
            ),
        )
        return suggestion
