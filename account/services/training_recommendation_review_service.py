from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, F, OuterRef, Q, Subquery
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from account.models import TrainingAdjustmentRecommendation
from account.portal_mixins import is_coach_only


class TrainingRecommendationReviewService:
    """Queries and audited decisions for coach progression recommendations."""

    APPROVED_STATUSES = (
        TrainingAdjustmentRecommendation.Status.ACCEPTED,
        TrainingAdjustmentRecommendation.Status.EDITED,
    )

    @staticmethod
    def visible_queryset(staff_user):
        queryset = TrainingAdjustmentRecommendation.objects.all()
        if is_coach_only(staff_user):
            queryset = queryset.filter(
                user__coach=staff_user,
                user__is_active=True,
                user__is_admin=False,
                user__is_coach=False,
            )
        return queryset

    @classmethod
    def pending_group_queryset(cls, staff_user):
        """Return one latest pending row for each athlete/exercise/slot group."""
        pending = cls.visible_queryset(staff_user).filter(
            status=TrainingAdjustmentRecommendation.Status.PENDING
        )
        latest_in_group = pending.filter(
            user_id=OuterRef("user_id"),
            exercise_id=OuterRef("exercise_id"),
            slot=OuterRef("slot"),
            recommendation_type=OuterRef("recommendation_type"),
        ).order_by("-created_at", "-pk")
        count_in_group = (
            pending.filter(
                user_id=OuterRef("user_id"),
                exercise_id=OuterRef("exercise_id"),
                slot=OuterRef("slot"),
                recommendation_type=OuterRef("recommendation_type"),
            )
            .order_by()
            .values("user_id")
            .annotate(group_count=Count("pk"))
            .values("group_count")[:1]
        )
        return (
            pending.annotate(
                latest_group_id=Subquery(latest_in_group.values("pk")[:1]),
                group_pending_count=Subquery(count_in_group),
            )
            .filter(pk=F("latest_group_id"))
            .select_related("user", "exercise", "source_session", "reviewed_by")
            .order_by("-created_at", "-pk")
        )

    @classmethod
    def pending_group_records(cls, staff_user, recommendation):
        return (
            cls.visible_queryset(staff_user)
            .filter(
                status=TrainingAdjustmentRecommendation.Status.PENDING,
                user_id=recommendation.user_id,
                exercise_id=recommendation.exercise_id,
                slot=recommendation.slot,
                recommendation_type=recommendation.recommendation_type,
            )
            .select_related("user", "exercise", "source_session", "reviewed_by")
            .order_by("-created_at", "-pk")
        )

    @classmethod
    @transaction.atomic
    def review(
        cls,
        *,
        staff_user,
        recommendation_id,
        status,
        reviewed_load,
        coach_note,
    ):
        allowed = {
            TrainingAdjustmentRecommendation.Status.ACCEPTED,
            TrainingAdjustmentRecommendation.Status.EDITED,
            TrainingAdjustmentRecommendation.Status.REJECTED,
        }
        if status not in allowed:
            raise ValidationError({"status": _("وضعیت بازبینی معتبر نیست.")})

        recommendation = (
            cls.visible_queryset(staff_user)
            .select_for_update()
            .filter(pk=recommendation_id)
            .first()
        )
        if recommendation is None:
            raise TrainingAdjustmentRecommendation.DoesNotExist
        if recommendation.status != TrainingAdjustmentRecommendation.Status.PENDING:
            raise ValidationError({"status": _("این پیشنهاد قبلاً از صف بازبینی خارج شده است.")})

        pending_group = list(
            cls.pending_group_records(staff_user, recommendation)
            .select_for_update()
        )
        if not pending_group or pending_group[0].pk != recommendation.pk:
            raise ValidationError({"status": _("پیشنهاد جدیدتری برای این حرکت وجود دارد؛ صف را تازه‌سازی کنید.")})

        is_deload = recommendation.recommendation_type == TrainingAdjustmentRecommendation.RecommendationType.DELOAD
        is_rep_progression = (
            isinstance(recommendation.evidence, dict)
            and recommendation.evidence.get("progression_strategy") == "add_reps_before_load"
        )
        load_optional = is_deload or (
            is_rep_progression and status == TrainingAdjustmentRecommendation.Status.ACCEPTED
        )
        if status == TrainingAdjustmentRecommendation.Status.EDITED and reviewed_load is None and not is_deload:
            raise ValidationError({"reviewed_load_kg": _("برای ثبت نسخهٔ اصلاح‌شده، بار موردنظر را وارد کنید.")})
        if status == TrainingAdjustmentRecommendation.Status.ACCEPTED and reviewed_load is None:
            reviewed_load = recommendation.suggested_load_kg
        if status in cls.APPROVED_STATUSES and not load_optional and (reviewed_load is None or reviewed_load <= 0):
            raise ValidationError({"reviewed_load_kg": _("برای استفاده در برنامهٔ بعدی، بار نهایی باید بیشتر از صفر باشد.")})
        if is_deload and status in cls.APPROVED_STATUSES and reviewed_load is None:
            reviewed_load = recommendation.suggested_load_kg
        if status == TrainingAdjustmentRecommendation.Status.REJECTED:
            reviewed_load = None
        if len(coach_note) > 2000:
            raise ValidationError({"coach_note": _("یادداشت مربی نباید بیشتر از ۲۰۰۰ نویسه باشد.")})

        reviewed_at = timezone.now()
        recommendation.status = status
        recommendation.reviewed_load_kg = reviewed_load
        recommendation.reviewed_by = staff_user
        recommendation.reviewed_at = reviewed_at
        recommendation.coach_note = coach_note
        recommendation.save(
            update_fields=[
                "status",
                "reviewed_load_kg",
                "reviewed_by",
                "reviewed_at",
                "coach_note",
            ]
        )

        older_ids = [item.pk for item in pending_group[1:]]
        if older_ids:
            cls.visible_queryset(staff_user).filter(pk__in=older_ids).update(
                status=TrainingAdjustmentRecommendation.Status.SUPERSEDED,
                superseded_by=recommendation,
                reviewed_by=staff_user,
                reviewed_at=reviewed_at,
            )
        return recommendation, len(older_ids)

    @classmethod
    def lock_applicable_recommendations(cls, *, athlete, recommendation_ids, program=None):
        """Lock and validate approved recommendations embedded in a signed preview."""
        ids = list(dict.fromkeys(int(value) for value in recommendation_ids))
        if not ids:
            return []
        queryset = TrainingAdjustmentRecommendation.objects.select_for_update().filter(
            pk__in=ids,
            user=athlete,
            status__in=cls.APPROVED_STATUSES,
        )
        if program and program.pk:
            queryset = queryset.filter(Q(applied_to_program__isnull=True) | Q(applied_to_program=program))
        else:
            queryset = queryset.filter(applied_to_program__isnull=True)
        rows = list(queryset.order_by("pk"))
        if len(rows) != len(ids):
            raise ValidationError(
                _("یکی از پیشنهادهای تأییدشدهٔ پیش‌نمایش تغییر کرده یا قبلاً در برنامه‌ای مصرف شده است؛ برنامه را دوباره تولید کنید.")
            )
        if any(
            item.recommendation_type != TrainingAdjustmentRecommendation.RecommendationType.DELOAD
            and not (
                isinstance(item.evidence, dict)
                and item.evidence.get("progression_strategy") == "add_reps_before_load"
            )
            and (item.reviewed_load_kg is None or item.reviewed_load_kg <= 0)
            for item in rows
        ):
            raise ValidationError(_("بار تأییدشدهٔ یکی از پیشنهادها معتبر نیست؛ برنامه را دوباره تولید کنید."))
        return rows

    @staticmethod
    def mark_applied(*, recommendations, program):
        pending_ids = [item.pk for item in recommendations if item.applied_to_program_id is None]
        if not pending_ids:
            return 0
        return TrainingAdjustmentRecommendation.objects.filter(
            pk__in=pending_ids,
            applied_to_program__isnull=True,
        ).update(
            applied_to_program=program,
            applied_at=timezone.now(),
            applied_program_revision=program.revision,
        )
