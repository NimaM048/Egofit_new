from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.utils.translation import gettext as _

from account.models import (
    ExerciseStrengthEstimate,
    TrainingAdjustmentRecommendation,
    WorkoutSession,
    WorkoutSetResult,
)


class ExerciseStrengthService:
    """Keep e1RM history and route sustained declines through coach review."""

    DECLINE_THRESHOLD = Decimal("0.03")

    @staticmethod
    def _effective_reps(result, experience_years):
        reps = Decimal(result.actual_reps or 0)
        if result.rir is not None:
            effort_reps = Decimal(min(result.rir, 5))
        elif result.rpe is not None:
            inferred_rir = Decimal(max(0, 10 - result.rpe))
            if experience_years is not None and experience_years < 1:
                inferred_rir *= Decimal("0.5")
            effort_reps = inferred_rir
        else:
            effort_reps = Decimal(0)
        return min(Decimal(15), reps + effort_reps)

    @classmethod
    @transaction.atomic
    def record_session(cls, session: WorkoutSession):
        if session.status != WorkoutSession.Status.COMPLETED:
            return []
        experience_years = getattr(getattr(session.user, "training_profile", None), "experience_years", None)
        results = list(
            session.sets.filter(
                exercise__isnull=False,
                completed=True,
                is_warmup=False,
                actual_load_kg__gt=0,
                actual_reps__gt=0,
                actual_reps__lte=12,
            ).select_related("exercise").order_by("exercise_id", "set_number", "pk")
        )
        estimates = []
        for result in results:
            effective_reps = cls._effective_reps(result, experience_years)
            if effective_reps <= 0:
                continue
            estimate_value = result.actual_load_kg * (Decimal(1) + effective_reps / Decimal(30))
            estimate_value = estimate_value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            estimate, created = ExerciseStrengthEstimate.objects.get_or_create(
                source_set=result,
                defaults={
                    "user_id": session.user_id,
                    "exercise_id": result.exercise_id,
                    "e1rm_kg": estimate_value,
                    "effective_reps": effective_reps,
                    "measured_on": session.started_at,
                },
            )
            if created:
                estimates.append(estimate)

        cls._create_deload_recommendations(session, results)
        return estimates

    @classmethod
    def _create_deload_recommendations(cls, session, current_results):
        current_exercises = {row.exercise_id for row in current_results if row.exercise_id}
        for exercise_id in current_exercises:
            history = ExerciseStrengthEstimate.objects.filter(
                user_id=session.user_id,
                exercise_id=exercise_id,
                source_set__session__status=WorkoutSession.Status.COMPLETED,
            ).select_related("source_set", "source_set__session", "exercise").order_by("-measured_on", "-pk")[:30]
            by_session = {}
            for item in history:
                session_id = item.source_set.session_id
                if session_id not in by_session and len(by_session) >= 3:
                    break
                current = by_session.get(session_id)
                # A session may contain several working sets. Compare the
                # strongest estimate from each session instead of whichever
                # set happened to be saved last.
                if current is None or item.e1rm_kg > current.e1rm_kg:
                    by_session[session_id] = item
            recent = list(by_session.values())
            if len(recent) < 3 or recent[0].source_set.session_id != session.pk:
                continue
            newest, previous, oldest = recent
            if not (
                newest.e1rm_kg <= previous.e1rm_kg * (Decimal(1) - cls.DECLINE_THRESHOLD)
                and previous.e1rm_kg <= oldest.e1rm_kg * (Decimal(1) - cls.DECLINE_THRESHOLD)
            ):
                continue
            source = newest.source_set
            suggested_load = (
                (source.actual_load_kg * Decimal("0.90")).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
                if source.actual_load_kg
                else None
            )
            TrainingAdjustmentRecommendation.objects.get_or_create(
                source_session=session,
                exercise_id=exercise_id,
                slot=WorkoutSetResult.Slot.MAIN,
                recommendation_type=TrainingAdjustmentRecommendation.RecommendationType.DELOAD,
                defaults={
                    "user_id": session.user_id,
                    "program_id": session.program_id,
                    "program_exercise_id": source.program_exercise_id,
                    "movement_name": source.planned_movement_name or source.exercise.name,
                    "confidence": TrainingAdjustmentRecommendation.Confidence.MEDIUM,
                    "baseline_load_kg": source.actual_load_kg,
                    "suggested_load_kg": suggested_load,
                    "rationale": str(_("برآورد e1RM در سه جلسهٔ جداگانه دو بار پیاپی دست‌کم ۳٪ کاهش داشته است. پیشنهاد کاهش ست‌ها برای این عضله و کم‌کردن بار حرکت لنگر ۱۰٪ برای بررسی مربی ثبت شد.")),
                    "evidence": {
                        "e1rm_estimates": [str(item.e1rm_kg) for item in recent],
                        "session_ids": [item.source_set.session_id for item in recent],
                        "measured_on": [item.measured_on.isoformat() for item in recent],
                        "consecutive_decline_threshold": str(cls.DECLINE_THRESHOLD),
                        "muscle_id": source.exercise.primary_muscle_id,
                        "set_multiplier": "0.5",
                        "anchor_load_multiplier": "0.9",
                    },
                    "algorithm_version": "e1rm-deload-v1",
                },
            )
