"""Read athlete readiness, preferences, and approved coaching decisions."""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Any, Mapping
from dataclasses import dataclass

from django.db.models import Q
from django.utils import timezone

from account.models import (
    AthleteExercisePreference,
    AthleteTrainingProfile,
    DailyMoodEntry,
    TrainingAdjustmentRecommendation,
    WorkoutSession,
    WorkoutSetResult,
)


@dataclass(frozen=True)
class AthleteEvidence:
    readiness: Mapping[str, Any]
    fatigue_scores: Mapping[int, float]
    preferences: Mapping[str, set[int]]
    last_performance: Mapping[int, dict[str, Any]]
    approved_loads: Mapping[int, TrainingAdjustmentRecommendation]
    approved_deloads: Mapping[int, TrainingAdjustmentRecommendation]
    approved_rep_progressions: Mapping[int, TrainingAdjustmentRecommendation]


def _training_profile(athlete: Any) -> AthleteTrainingProfile | None:
    if isinstance(athlete, AthleteTrainingProfile):
        return athlete
    user_id = getattr(athlete, "pk", None)
    if not user_id:
        return None
    return AthleteTrainingProfile.objects.filter(user_id=user_id).prefetch_related("available_equipment").first()


def _readiness_snapshot(athlete_id: int | None) -> dict[str, Any]:
    if not athlete_id:
        return {"score": None, "samples": 0, "low": False}
    cutoff = timezone.localdate() - timedelta(days=6)
    entries = list(
        DailyMoodEntry.objects.filter(
            user_id=athlete_id,
            entry_date__gte=cutoff,
        ).filter(
            Q(training_readiness_score__isnull=False)
            | Q(fatigue_score__isnull=False)
            | Q(muscle_soreness_score__isnull=False)
        ).order_by("-entry_date", "-pk")[:3]
    )
    if len(entries) < 2:
        return {"score": None, "samples": len(entries), "low": False}
    metrics = (
        ("readiness", "training_readiness_score", 0.40, False),
        ("sleep", "sleep_quality_score", 0.25, False),
        ("fatigue", "fatigue_score", 0.175, True),
        ("soreness", "muscle_soreness_score", 0.175, True),
    )
    metric_averages = {}
    weighted_total = 0.0
    available_weight = 0.0
    for name, field, weight, inverse in metrics:
        values = [getattr(entry, field) for entry in entries if getattr(entry, field) is not None]
        if not values:
            continue
        average = sum(values) / len(values)
        metric_averages[name] = round(average, 1)
        weighted_total += (10 - average if inverse else average) * weight
        available_weight += weight
    score = round(weighted_total / available_weight * 1.0, 1) if available_weight else None
    return {
        "score": score,
        **{f"{name}_average": value for name, value in metric_averages.items()},
        "samples": len(entries),
        "entry_dates": [entry.entry_date.isoformat() for entry in entries],
        "low": score is not None and score <= 4.5,
    }


def _approved_progression_loads(athlete_id: int | None) -> dict[int, TrainingAdjustmentRecommendation]:
    if not athlete_id:
        return {}
    cutoff = timezone.now() - timedelta(days=42)
    result = {}
    rows = TrainingAdjustmentRecommendation.objects.filter(
        user_id=athlete_id,
        recommendation_type=TrainingAdjustmentRecommendation.RecommendationType.INCREASE_LOAD,
        status__in=(
            TrainingAdjustmentRecommendation.Status.ACCEPTED,
            TrainingAdjustmentRecommendation.Status.EDITED,
        ),
        reviewed_load_kg__gt=0,
        reviewed_at__gte=cutoff,
        applied_to_program__isnull=True,
    ).select_related("exercise").order_by("-reviewed_at", "-pk")
    for row in rows:
        result.setdefault(row.exercise_id, row)
    return result


def _approved_deload_recommendations(athlete_id: int | None) -> dict[int, TrainingAdjustmentRecommendation]:
    if not athlete_id:
        return {}
    rows = TrainingAdjustmentRecommendation.objects.filter(
        user_id=athlete_id,
        recommendation_type=TrainingAdjustmentRecommendation.RecommendationType.DELOAD,
        status__in=(TrainingAdjustmentRecommendation.Status.ACCEPTED, TrainingAdjustmentRecommendation.Status.EDITED),
        reviewed_at__gte=timezone.now() - timedelta(days=42),
        applied_to_program__isnull=True,
    ).select_related("exercise").order_by("-reviewed_at", "-pk")
    result = {}
    for row in rows:
        result.setdefault(row.exercise_id, row)
    return result


def _approved_rep_progressions(athlete_id: int | None) -> dict[int, TrainingAdjustmentRecommendation]:
    if not athlete_id:
        return {}
    rows = TrainingAdjustmentRecommendation.objects.filter(
        user_id=athlete_id,
        recommendation_type=TrainingAdjustmentRecommendation.RecommendationType.HOLD,
        status__in=(TrainingAdjustmentRecommendation.Status.ACCEPTED, TrainingAdjustmentRecommendation.Status.EDITED),
        reviewed_at__gte=timezone.now() - timedelta(days=42),
        applied_to_program__isnull=True,
        evidence__progression_strategy="add_reps_before_load",
    ).select_related("exercise").order_by("-reviewed_at", "-pk")
    result = {}
    for row in rows:
        result.setdefault(row.exercise_id, row)
    return result


def _exercise_preferences(athlete_id: int | None) -> dict[str, set[int]]:
    if not athlete_id:
        return {"favorite": set(), "avoid": set(), "pain": set()}
    result = {"favorite": set(), "avoid": set(), "pain": set()}
    for exercise_id, status in AthleteExercisePreference.objects.filter(user_id=athlete_id).values_list("exercise_id", "status"):
        result.setdefault(status, set()).add(exercise_id)
    return result


def _muscle_fatigue_snapshot(athlete_id: int | None, experience_years=None) -> dict[int, dict[str, Any]]:
    if not athlete_id:
        return {}
    cutoff = timezone.now() - timedelta(days=14)
    totals = defaultdict(float)
    labels = {}
    now = timezone.now()
    rows = WorkoutSetResult.objects.filter(
        session__user_id=athlete_id,
        session__status=WorkoutSession.Status.COMPLETED,
        session__started_at__gte=cutoff,
        completed=True,
        exercise__isnull=False,
        is_warmup=False,
    ).select_related("session", "exercise__primary_muscle", "exercise__secondary_muscle")
    for result in rows:
        age_hours = max(0.0, (now - result.session.started_at).total_seconds() / 3600)
        decay = 0.5 ** (age_hours / 48.0)
        if result.rir is not None:
            effort = 10 - result.rir
        elif result.rpe is not None:
            effort = 7 + (result.rpe - 7) * (0.5 if experience_years is not None and experience_years < 1 else 1)
        else:
            effort = 7
        intensity = max(0.55, min(1.45, 1 + (effort - 7) * 0.10))
        stress = decay * intensity
        primary = result.exercise.primary_muscle
        if primary:
            totals[primary.pk] += stress
            labels[primary.pk] = primary.name
        secondary = result.exercise.secondary_muscle
        if secondary and (not primary or secondary.pk != primary.pk):
            totals[secondary.pk] += stress * 0.5
            labels[secondary.pk] = secondary.name
    return {
        muscle_id: {
            "name": labels[muscle_id],
            "fatigue": round(min(100, value * 18), 1),
            "recovery": round(max(0, 100 - min(100, value * 18)), 1),
        }
        for muscle_id, value in totals.items()
    }


def _last_performance_snapshot(athlete_id: int | None) -> dict[int, dict[str, Any]]:
    if not athlete_id:
        return {}
    cutoff = timezone.now() - timedelta(days=90)
    rows = WorkoutSetResult.objects.filter(
        session__user_id=athlete_id,
        session__status=WorkoutSession.Status.COMPLETED,
        session__started_at__gte=cutoff,
        exercise__isnull=False,
        completed=True,
        is_warmup=False,
    ).filter(Q(actual_load_kg__isnull=False) | Q(actual_reps__isnull=False)).select_related("session").order_by(
        "exercise_id", "-session__started_at", "-pk"
    )
    result = {}
    for row in rows:
        result.setdefault(row.exercise_id, {
            "date": timezone.localtime(row.session.started_at).date().isoformat(),
            "load_kg": str(row.actual_load_kg) if row.actual_load_kg is not None else None,
            "reps": row.actual_reps,
            "rpe": row.rpe,
            "rir": row.rir,
        })
    return result
