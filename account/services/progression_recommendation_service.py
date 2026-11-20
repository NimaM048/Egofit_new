from __future__ import annotations

from collections import OrderedDict
from datetime import timedelta
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from account.models import (
    TrainingAdjustmentRecommendation,
    WorkoutSession,
    WorkoutSetResult,
)


class ProgressionRecommendationService:
    """Create coach-review suggestions from recent, set-level training evidence."""

    ALGORITHM_VERSION = "progression-v1"
    LOOKBACK_DAYS = 42
    MAX_EXPOSURES = 3

    @classmethod
    @transaction.atomic
    def create_for_session(cls, session: WorkoutSession):
        current_results = list(
            session.sets.filter(exercise__isnull=False, is_warmup=False)
            .select_related("exercise", "program_exercise")
            .order_by("slot", "exercise_id", "set_number")
        )
        current_groups = OrderedDict()
        for result in current_results:
            current_groups.setdefault((result.exercise_id, result.slot), []).append(result)

        cutoff = timezone.now() - timedelta(days=cls.LOOKBACK_DAYS)
        experience_years = getattr(getattr(session.user, "training_profile", None), "experience_years", None)
        rpe_weight = 0.5 if experience_years is not None and experience_years < 1 else 1.0
        recommendations = []
        for (exercise_id, slot), recent_session_rows in current_groups.items():
            rows = list(
                WorkoutSetResult.objects.filter(
                    session__user_id=session.user_id,
                    session__status=WorkoutSession.Status.COMPLETED,
                    session__started_at__gte=cutoff,
                    exercise_id=exercise_id,
                    slot=slot,
                    is_warmup=False,
                )
                .select_related("session")
                .order_by("-session__started_at", "-session_id", "set_number")
            )
            exposures = OrderedDict()
            for row in rows:
                if row.session_id not in exposures and len(exposures) >= cls.MAX_EXPOSURES:
                    break
                exposures.setdefault(row.session_id, []).append(row)
            exposure_groups = list(exposures.items())[: cls.MAX_EXPOSURES]
            if not exposure_groups or exposure_groups[0][0] != session.pk:
                continue

            outcomes = [cls._evaluate_exposure(exposure_rows, rpe_weight=rpe_weight) for _, exposure_rows in exposure_groups]
            baseline = cls._baseline_load(exposure_groups[0][1])
            representative = recent_session_rows[0]
            increment = getattr(getattr(representative.exercise, "equipment_type", None), "load_increment_kg", None)
            decision, confidence, suggested_load, rationale = cls._recommend(outcomes, baseline, increment)
            evidence = {
                "exposure_count": len(outcomes),
                "session_ids": [session_id for session_id, _ in exposure_groups],
                "outcomes": outcomes,
                "lookback_days": cls.LOOKBACK_DAYS,
                "required_exposures_for_progression": 2,
                "experience_years": experience_years,
                "rpe_weight": rpe_weight,
                "load_increment_kg": str(increment) if increment is not None else "0.5",
            }
            if rationale.startswith("equipment_step:"):
                next_rep_target = max(
                    (item.get("planned_reps_max") or 0 for item in outcomes[:2]),
                    default=0,
                ) + 1
                evidence["progression_strategy"] = "add_reps_before_load"
                evidence["next_rep_target"] = next_rep_target or None
                rationale = str(_("گام بعدی وزنه نسبت به بار فعلی بیش از ۵٪ است؛ پیش از افزایش وزنه، تکرارها یک واحد بالا بروند و سپس مربی دوباره بررسی کند."))
            else:
                evidence["progression_strategy"] = "increase_load" if decision == TrainingAdjustmentRecommendation.RecommendationType.INCREASE_LOAD else "hold_or_review"
            recommendation, _created = TrainingAdjustmentRecommendation.objects.get_or_create(
                source_session=session,
                exercise=representative.exercise,
                slot=slot,
                recommendation_type=decision,
                defaults={
                    "user_id": session.user_id,
                    "program": session.program,
                    "program_exercise": representative.program_exercise,
                    "movement_name": representative.planned_movement_name or representative.exercise.name,
                    "recommendation_type": decision,
                    "confidence": confidence,
                    "baseline_load_kg": baseline,
                    "suggested_load_kg": suggested_load,
                    "rationale": rationale,
                    "evidence": evidence,
                    "algorithm_version": cls.ALGORITHM_VERSION,
                },
            )
            recommendations.append(recommendation)
        session._progression_recommendations = recommendations
        return recommendations

    @staticmethod
    def _evaluate_exposure(rows, *, rpe_weight=1.0):
        completed_rows = [row for row in rows if row.completed]
        actual_reps = [row.actual_reps for row in completed_rows if row.actual_reps is not None]
        minimum_targets = [row.planned_reps_min for row in rows if row.planned_reps_min is not None]
        maximum_targets = [row.planned_reps_max for row in rows if row.planned_reps_max is not None]
        rpe = [row.rpe for row in completed_rows if row.rpe is not None]
        rir = [row.rir for row in completed_rows if row.rir is not None]
        adjusted_rpe = [7 + (value - 7) * rpe_weight for value in rpe]
        average_rpe = sum(adjusted_rpe) / len(adjusted_rpe) if adjusted_rpe else None
        average_rir = sum(rir) / len(rir) if rir else None
        met_maximum = bool(
            rows
            and len(completed_rows) == len(rows)
            and len(actual_reps) == len(rows)
            and len(maximum_targets) == len(rows)
        ) and all(
            row.actual_reps >= row.planned_reps_max for row in rows
        )
        below_minimum = any(
            row.actual_reps is not None
            and row.planned_reps_min is not None
            and row.actual_reps < row.planned_reps_min
            for row in completed_rows
        )
        effort_values = []
        if average_rpe is not None:
            effort_values.append(average_rpe <= 8)
        if average_rir is not None:
            effort_values.append(average_rir >= 2)
        effort_ok = bool(effort_values) and all(effort_values)
        effort_high = (average_rpe is not None and average_rpe >= 9) or (average_rir is not None and average_rir <= 1)
        if met_maximum and effort_ok:
            outcome = "completed_at_target_effort"
        elif below_minimum or effort_high:
            outcome = "below_target_or_high_effort"
        else:
            outcome = "mixed_or_effort_missing"
        return {
            "session_id": rows[0].session_id,
            "outcome": outcome,
            "completed_sets": len(completed_rows),
            "average_reps": round(sum(actual_reps) / len(actual_reps), 2) if actual_reps else None,
            "planned_reps_min": min(minimum_targets) if minimum_targets else None,
            "planned_reps_max": max(maximum_targets) if maximum_targets else None,
            "average_rpe": round(average_rpe, 2) if average_rpe is not None else None,
            "average_rir": round(average_rir, 2) if average_rir is not None else None,
            "effort_data_present": bool(rpe or rir),
            "all_sets_completed": bool(rows) and len(completed_rows) == len(rows),
        }

    @staticmethod
    def _baseline_load(rows):
        actual = [row.actual_load_kg for row in rows if row.completed and row.actual_load_kg is not None and row.actual_load_kg > 0]
        if actual:
            return sum(actual) / Decimal(len(actual))
        planned = [row.planned_load_kg for row in rows if row.planned_load_kg is not None and row.planned_load_kg > 0]
        return sum(planned) / Decimal(len(planned)) if planned else None

    @classmethod
    def _recommend(cls, outcomes, baseline, equipment_increment=None):
        if len(outcomes) < 2:
            return (
                TrainingAdjustmentRecommendation.RecommendationType.REVIEW,
                TrainingAdjustmentRecommendation.Confidence.LOW,
                None,
                str(_("فقط یک مواجههٔ اخیر در دسترس است؛ برای تصمیم دربارهٔ تغییر بار، چند جلسهٔ دیگر ثبت شود.")),
            )

        recent = outcomes[:2]
        if all(item["outcome"] == "completed_at_target_effort" for item in recent):
            if baseline is None:
                return (
                    TrainingAdjustmentRecommendation.RecommendationType.HOLD,
                    TrainingAdjustmentRecommendation.Confidence.MEDIUM,
                    None,
                    str(_("دو مواجههٔ اخیر به سقف تکرار با تلاش هدف رسیدند، اما بار مبنا ثبت نشده است؛ مربی ابتدا بار را مشخص کند.")),
                )
            step = Decimal(str(equipment_increment or "0.5"))
            steps = (baseline / step).quantize(Decimal("1"), rounding=ROUND_DOWN) + 1
            suggested = (steps * step).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if baseline > 0 and (suggested - baseline) / baseline > Decimal("0.05"):
                current_rep_target = max(
                    (item.get("planned_reps_max") or 0 for item in recent[:2]),
                    default=0,
                )
                if current_rep_target >= 15:
                    return (
                        TrainingAdjustmentRecommendation.RecommendationType.REVIEW,
                        TrainingAdjustmentRecommendation.Confidence.MEDIUM,
                        None,
                        str(_("پس از رسیدن به سقف ۱۵ تکرار، گام بعدی وزنه هنوز بیش از ۵٪ است؛ مربی تجهیزات یا حرکت جایگزین را بررسی کند.")),
                    )
                return (
                    TrainingAdjustmentRecommendation.RecommendationType.HOLD,
                    TrainingAdjustmentRecommendation.Confidence.MEDIUM,
                    None,
                    "equipment_step: " + str(_("گام بعدی وزنه در تجهیزات موجود بیش از ۵٪ است؛ تکرارها ابتدا افزایش یابند.")),
                )
            confidence = (
                TrainingAdjustmentRecommendation.Confidence.HIGH
                if len(outcomes) >= 3
                else TrainingAdjustmentRecommendation.Confidence.MEDIUM
            )
            return (
                TrainingAdjustmentRecommendation.RecommendationType.INCREASE_LOAD,
                confidence,
                suggested,
                str(_("دو مواجههٔ کامل اخیر همهٔ ست‌ها را در سقف تکرار و با RPE حداکثر ۸ یا RIR حداقل ۲ ثبت کردند؛ افزایش کوچک بار برای بازبینی مربی پیشنهاد می‌شود.")),
            )

        if all(item["outcome"] == "below_target_or_high_effort" for item in recent):
            return (
                TrainingAdjustmentRecommendation.RecommendationType.REVIEW,
                TrainingAdjustmentRecommendation.Confidence.MEDIUM,
                None,
                str(_("در دو مواجههٔ اخیر تکرار هدف کامل نشد یا تلاش بسیار بالا ثبت شد؛ مربی پیش از تغییر نسخه، اجرا و بازیابی را بررسی کند.")),
            )

        return (
            TrainingAdjustmentRecommendation.RecommendationType.HOLD,
            TrainingAdjustmentRecommendation.Confidence.LOW if len(outcomes) < 3 else TrainingAdjustmentRecommendation.Confidence.MEDIUM,
            None,
            str(_("شواهد اخیر یکدست نیست یا RPE/RIR کافی ندارد؛ نسخهٔ فعلی حفظ و دادهٔ بیشتری ثبت شود.")),
        )

    @staticmethod
    def serialize(recommendation):
        return {
            "id": recommendation.pk,
            "program_id": recommendation.program_id,
            "source_session_id": recommendation.source_session_id,
            "program_exercise_id": recommendation.program_exercise_id,
            "exercise_id": recommendation.exercise_id,
            "movement_name": recommendation.movement_name,
            "slot": recommendation.slot,
            "type": recommendation.recommendation_type,
            "status": recommendation.status,
            "confidence": recommendation.confidence,
            "baseline_load_kg": str(recommendation.baseline_load_kg) if recommendation.baseline_load_kg is not None else None,
            "suggested_load_kg": str(recommendation.suggested_load_kg) if recommendation.suggested_load_kg is not None else None,
            "reviewed_load_kg": str(recommendation.reviewed_load_kg) if recommendation.reviewed_load_kg is not None else None,
            "rationale": recommendation.rationale,
            "evidence": recommendation.evidence,
            "algorithm_version": recommendation.algorithm_version,
            "coach_note": recommendation.coach_note,
            "reviewed_at": timezone.localtime(recommendation.reviewed_at).isoformat() if recommendation.reviewed_at else None,
        }
