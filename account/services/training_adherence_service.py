from __future__ import annotations

from datetime import timedelta
from math import ceil

from django.db.models import Count, Q
from django.utils import timezone

from account.models import WorkoutProgram, WorkoutSession


class TrainingAdherenceService:
    LOOKBACK_DAYS = 30
    LOW_ADHERENCE_PERCENT = 50

    @classmethod
    def snapshot(cls, athlete):
        today = timezone.localdate()
        program = (
            WorkoutProgram.objects.filter(
                user=athlete,
                is_published=True,
                start_date__lte=today,
            ).filter(Q(end_date__isnull=True) | Q(end_date__gte=today))
            .annotate(day_count=Count("days", distinct=True))
            .filter(day_count__gt=0)
            .order_by("-start_date", "-pk")
            .first()
        )
        if not program:
            return {"available": False, "percent": None, "completed_sessions": 0, "expected_sessions": 0}
        cutoff = timezone.now() - timedelta(days=cls.LOOKBACK_DAYS)
        completed = WorkoutSession.objects.filter(
            user=athlete,
            program=program,
            status=WorkoutSession.Status.COMPLETED,
            started_at__gte=cutoff,
        ).count()
        expected = max(1, ceil(program.day_count * cls.LOOKBACK_DAYS / 7))
        percent = min(100, round(completed * 100 / expected))
        return {
            "available": True,
            "program_id": program.pk,
            "program_title": program.title,
            "lookback_days": cls.LOOKBACK_DAYS,
            "completed_sessions": completed,
            "expected_sessions": expected,
            "percent": percent,
            "low": percent < cls.LOW_ADHERENCE_PERCENT,
        }

    @classmethod
    def effective_sessions(cls, requested, snapshot):
        if not snapshot.get("low"):
            return requested
        # A floor of one session keeps the plan actionable when adherence is low.
        return max(1, min(requested, ceil(requested * snapshot["percent"] / 100)))
