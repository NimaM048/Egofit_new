"""Fit sessions to time and calculate training volume."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, ROUND_DOWN
import math
import re
from typing import Any, Mapping, Sequence

from django.utils.translation import gettext_lazy as _

from account.domain.exercise_goals import training_goal_for_program_goal
from account.models import Exercise, AthleteTrainingProfile, MuscleVolumeLandmark, Muscle
from account.utils import normalize_digits

from .catalog import GOAL_ALIASES, _is_compound_exercise, _label_contains
from .prescriptions import _set_count, _format_set_range


def _reduce_sets(value: Any, *, factor: Decimal | None = None, decrement: int = 0) -> str:
    low, high = _set_count(value)
    if high <= 0:
        return str(value or "")
    if factor is not None:
        low = max(1, int((Decimal(low) * factor).quantize(Decimal("1"), rounding=ROUND_DOWN)))
        high = max(low, int((Decimal(high) * factor).quantize(Decimal("1"), rounding=ROUND_DOWN)))
    else:
        low = max(1, low - decrement)
        high = max(low, high - decrement)
    return _format_set_range(low, high)


def _estimated_session_minutes(items: Sequence[dict[str, Any]]) -> int:
    seconds = 5 * 60 + len(items) * 30  # warm-up and transitions
    for item in items:
        low_sets, high_sets = _set_count(item.get("sets"))
        sets = max(1, (low_sets + high_sets) / 2)
        rep_values = [int(value) for value in re.findall(r"\d+", normalize_digits(item.get("reps") or ""))]
        reps = sum(rep_values[:2]) / min(2, len(rep_values)) if rep_values else 10
        rest_values = [int(value) for value in re.findall(r"\d+", normalize_digits(item.get("rest") or ""))]
        rest = sum(rest_values[:2]) / min(2, len(rest_values)) if rest_values else 90
        seconds += sets * ((reps * 3) + rest)
    return max(1, int((seconds + 59) // 60))


def _fit_session_duration(
    items: list[dict[str, Any]],
    *,
    maximum_minutes: int | None,
    exercises_by_id: Mapping[int, Exercise],
) -> tuple[list[dict[str, Any]], int, list[str]]:
    if not maximum_minutes:
        return items, _estimated_session_minutes(items), []
    warnings = []
    estimate = _estimated_session_minutes(items)
    while estimate > maximum_minutes:
        changed = False
        for item in reversed(items):
            low, high = _set_count(item.get("sets"))
            if low > 2:
                base_sets = item.get("sets")
                item["sets"] = _reduce_sets(base_sets, decrement=1)
                item["superset_sets"] = _reduce_sets(item.get("superset_sets") or base_sets, decrement=1)
                item["third_sets"] = _reduce_sets(item.get("third_sets") or base_sets, decrement=1)
                item["selection_reason"] = " ".join(filter(None, (
                    item.get("selection_reason", ""),
                    str(_("تعداد ست برای نزدیک‌شدن به زمان در دسترس کاهش یافت.")),
                )))
                changed = True
        if changed:
            estimate = _estimated_session_minutes(items)
            continue
        removable = next(
            (
                item for item in reversed(items)
                if len(items) > 2
                and (
                    exercises_by_id.get(int(item.get("exercise") or 0)) is None
                    or not _is_compound_exercise(exercises_by_id[int(item["exercise"])])
                )
            ),
            None,
        )
        if removable is None:
            break
        items.remove(removable)
        estimate = _estimated_session_minutes(items)
        warnings.append(str(_("یک حرکت کمکی کم‌اولویت برای رعایت مدت جلسه حذف شد؛ پیش از انتشار ترکیب روز را بررسی کنید.")))
    if estimate > maximum_minutes:
        warnings.append(
            str(_("مدت برآوردی جلسه %(estimate)s دقیقه است و از زمان ثبت‌شده (%(limit)s دقیقه) بیشتر می‌شود؛ مربی آن را بازبینی کند."))
            % {"estimate": estimate, "limit": maximum_minutes}
        )
    return items, estimate, warnings


def _weekly_muscle_volume(
    days: Sequence[dict[str, Any]],
    exercises_by_id: Mapping[int, Exercise],
    week: int | None = None,
) -> dict[str, dict[str, Any]]:
    totals = defaultdict(lambda: {"direct_min": 0.0, "direct_max": 0.0, "indirect_min": 0.0, "indirect_max": 0.0})
    labels = {}
    for day in days:
        for item in day.get("items", []):
            exercise = exercises_by_id.get(int(item.get("exercise") or 0))
            if not exercise:
                continue
            set_value = (
                (item.get("weekly_set_targets") or {}).get(str(week), item.get("sets"))
                if week is not None
                else item.get("sets")
            )
            low, high = _set_count(set_value)
            if exercise.primary_muscle_id:
                muscle_id = exercise.primary_muscle_id
                totals[muscle_id]["direct_min"] += low
                totals[muscle_id]["direct_max"] += high
                labels[muscle_id] = exercise.primary_muscle.name
            if exercise.secondary_muscle_id and exercise.secondary_muscle_id != exercise.primary_muscle_id:
                muscle_id = exercise.secondary_muscle_id
                totals[muscle_id]["indirect_min"] += low * 0.5
                totals[muscle_id]["indirect_max"] += high * 0.5
                labels[muscle_id] = exercise.secondary_muscle.name
    return {
        labels[muscle_id]: {
            "direct_sets": [round(values["direct_min"], 1), round(values["direct_max"], 1)],
            "indirect_sets": [round(values["indirect_min"], 1), round(values["indirect_max"], 1)],
            "weighted_total_sets": [
                round(values["direct_min"] + values["indirect_min"], 1),
                round(values["direct_max"] + values["indirect_max"], 1),
            ],
        }
        for muscle_id, values in sorted(totals.items(), key=lambda row: labels.get(row[0], ""))
    }


def _weekly_volume_target(
    goal: str,
    profile: AthleteTrainingProfile | None,
    muscle: Muscle | None = None,
    training_goal: str | None = None,
) -> dict[str, Any]:
    if _label_contains(goal, GOAL_ALIASES["volume"]):
        goal_band = "hypertrophy"
        ranges = {
            "beginner": (6, 14),
            "intermediate": (8, 18),
            "experienced": (10, 20),
            "unspecified": (8, 18),
        }
    elif _label_contains(goal, GOAL_ALIASES["strength"]) or _label_contains(
        goal, GOAL_ALIASES["power"]
    ):
        goal_band = "strength_power"
        ranges = {
            "beginner": (3, 8),
            "intermediate": (4, 10),
            "experienced": (5, 12),
            "unspecified": (4, 10),
        }
    else:
        goal_band = "general"
        ranges = {
            "beginner": (4, 12),
            "intermediate": (6, 14),
            "experienced": (6, 16),
            "unspecified": (6, 14),
        }
    years = profile.experience_years if profile else None
    experience_band = (
        "beginner" if years is not None and years <= 1
        else "intermediate" if years == 2
        else "experienced" if years is not None
        else "unspecified"
    )
    minimum, maximum = ranges[experience_band]
    default_target = {
        "minimum": minimum,
        "optimum": int((minimum + maximum) / 2),
        "maximum": maximum,
        "goal_band": goal_band,
        "experience_band": experience_band,
        "source": "fallback",
    }
    if not muscle:
        return default_target
    resolved_training_goal = training_goal_for_program_goal(training_goal or goal)
    target = MuscleVolumeLandmark.objects.filter(
        muscle=muscle,
        training_goal=resolved_training_goal,
        experience_band=experience_band,
    ).first()
    if target:
        return {
            "minimum": target.mev,
            "optimum": target.mav,
            "maximum": target.mrv,
            "goal_band": resolved_training_goal,
            "experience_band": experience_band,
            "source": "library",
        }
    return default_target


def _apply_weekly_volume_progression(
    days: Sequence[dict[str, Any]],
    exercises_by_id: Mapping[int, Exercise],
    current_volume: Mapping[str, dict[str, Any]],
    targets: Mapping[str, dict[str, Any]],
    cycle_weeks: int = 4,
) -> None:
    """Move primary-muscle set counts from current volume toward MAV, then deload."""
    for muscle_name, target in targets.items():
        volume = current_volume.get(muscle_name)
        if not volume:
            continue
        lower, upper = volume["weighted_total_sets"]
        minimum_add = min(
            max(0, math.ceil(float(target.get("minimum", lower)) - lower)),
            max(0, int(target["maximum"] - upper)),
        )
        ramp_headroom = max(
            0,
            min(
                math.ceil(float(target["optimum"] - lower - minimum_add)),
                int(target["maximum"] - upper - minimum_add),
            ),
        )
        items = [
            item
            for day in days
            for item in day.get("items", [])
            if (exercise := exercises_by_id.get(int(item.get("exercise") or 0)))
            and exercise.primary_muscle
            and exercise.primary_muscle.name == muscle_name
        ]
        if not items:
            continue

        def allocations(count):
            each, remainder = divmod(max(0, count), len(items))
            return [each + (1 if index < remainder else 0) for index in range(len(items))]

        build_weeks = max(1, cycle_weeks - 1)
        for index, item in enumerate(items):
            base_low, base_high = _set_count(item.get("sets"))
            if base_low <= 0:
                continue
            item_targets = {}
            for week in range(1, cycle_weeks + 1):
                if week == cycle_weeks and cycle_weeks > 1:
                    added = allocations(minimum_add + ramp_headroom)[index]
                    counts = (
                        max(1, int((base_low + added) * 0.5)),
                        max(1, int((base_high + added) * 0.5)),
                    )
                else:
                    ramp_add = int(ramp_headroom * max(0, week - 1) / max(1, build_weeks - 1))
                    added_total = minimum_add + ramp_add
                    added = allocations(added_total)[index]
                    counts = (base_low + added, base_high + added)
                item_targets[str(week)] = _format_set_range(*counts)
            item["weekly_set_targets"] = item_targets
