"""Build the editable preview for a cardio program."""

from __future__ import annotations

import hashlib
import json
import random
from typing import Any, Mapping

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from account.models import (
    CardioExercise,
    ExerciseTrainingMethod,
    AthleteTrainingProfile,
    WorkoutProgramDay,
)

from . import ALGORITHM_VERSION


def generate_cardio(
    *,
    sessions: int,
    goal: str,
    profile: AthleteTrainingProfile | None,
    equipment_ids: set[int],
    filter_by_equipment: bool,
    rng: random.Random,
    seed_value: str,
    readiness: Mapping[str, Any] | None = None,
    adherence: Mapping[str, Any] | None = None,
    training_goal: str | None = None,
    training_goal_source: str | None = None,
) -> dict[str, Any]:
    readiness = readiness or {"score": None, "samples": 0, "low": False}
    adherence = adherence or {"available": False, "percent": None, "low": False}
    cardio_exercises = list(
        CardioExercise.objects.prefetch_related("required_equipment_types").all()
    )
    unclassified_equipment = 0
    if filter_by_equipment:
        eligible = []
        for exercise in cardio_exercises:
            required = {item.pk for item in exercise.required_equipment_types.all()}
            if required and required - equipment_ids:
                continue
            if not required:
                unclassified_equipment += 1
            eligible.append(exercise)
        cardio_exercises = eligible
    if not cardio_exercises:
        raise ValidationError(_("با تجهیزات ثبت‌شده حرکت هوازی سازگار پیدا نشد؛ کتابخانه یا تجهیزات ورزشکار را بررسی کنید."))

    try:
        session_count = max(1, min(7, int(sessions)))
    except (TypeError, ValueError):
        session_count = 1

    days = []
    unused = list(cardio_exercises)
    rng.shuffle(unused)
    for day_number in range(1, session_count + 1):
        if not unused:
            unused = list(cardio_exercises)
            rng.shuffle(unused)
        cardio_exercise = unused.pop()
        is_interval = cardio_exercise.performance_type == "interval"
        intervals = 6 if is_interval else None
        work_seconds = 60 if is_interval else None
        recovery_seconds = 90 if is_interval else None
        duration_seconds = intervals * (work_seconds + recovery_seconds) if is_interval else 1200
        if profile and profile.session_duration_minutes:
            maximum_duration = max(300, profile.session_duration_minutes * 60 - 5 * 60)
            if is_interval:
                intervals = max(1, min(intervals, maximum_duration // (work_seconds + recovery_seconds)))
                duration_seconds = intervals * (work_seconds + recovery_seconds)
            else:
                duration_seconds = min(duration_seconds, maximum_duration)
        if readiness.get("low"):
            duration_seconds = max(300, int(duration_seconds * 0.9))
            if is_interval:
                intervals = max(1, duration_seconds // (work_seconds + recovery_seconds))
                duration_seconds = intervals * (work_seconds + recovery_seconds)
        estimated_minutes = max(1, (duration_seconds + 5 * 60 + 59) // 60)
        days.append(
            {
                "name": f"روز {day_number}",
                "notes": f"نسخهٔ عمومی برای بازبینی مربی؛ مدت برآوردی با گرم‌کردن: {estimated_minutes} دقیقه.",
                "estimated_duration_minutes": estimated_minutes,
                "items": [
                    {
                        "exercise": cardio_exercise.pk,
                        "exercise_type": "cardio",
                        "exercise_name": cardio_exercise.name,
                        "body_part_filter": "cardio",
                        "goal": goal,
                        "superset_exercise": "",
                        "third_exercise": "",
                        "sets": "1",
                        "reps": "",
                        "rest": "",
                        "superset_sets": "",
                        "superset_reps": "",
                        "superset_rest": "",
                        "third_sets": "",
                        "third_reps": "",
                        "third_rest": "",
                        "note": str(cardio_exercise.get_performance_type_display()),
                        "training_method": ExerciseTrainingMethod.NORMAL,
                        "cardio_duration_seconds": duration_seconds,
                        "cardio_interval_count": intervals,
                        "cardio_work_seconds": work_seconds,
                        "cardio_recovery_seconds": recovery_seconds,
                        "target_rpe": 6 if is_interval else 5,
                        "selection_reason": (
                            _("نوع اجرای حرکت با نسخهٔ هوازی %(type)s هماهنگ است.")
                            % {"type": cardio_exercise.get_performance_type_display()}
                        ),
                        "selection_score": 85 if equipment_ids else 75,
                    }
                ],
            }
        )
    valid_days = [day for day in (profile.preferred_days or []) if day in WorkoutProgramDay.Weekday.values] if profile else []
    weekday_labels = dict(WorkoutProgramDay.Weekday.choices)
    for index, day in enumerate(days):
        if index < len(valid_days):
            day["scheduled_weekday"] = valid_days[index]
            day["schedule_label"] = str(weekday_labels[valid_days[index]])
    warnings = []
    if not profile or not equipment_ids:
        warnings.append(_("اطلاعات پروفایل یا تجهیزات کامل نیست؛ پیشنهاد هوازی را مربی بازبینی کند."))
    if unclassified_equipment:
        warnings.append(_("تجهیزات %(count)s حرکت هوازی در کتابخانه مشخص نشده است.") % {"count": unclassified_equipment})
    if readiness.get("low"):
        warnings.append(_("آمادگی و خواب هفتهٔ اخیر پایین است؛ مدت هوازی ۱۰٪ کم شد و مربی باید آن را بازبینی کند."))
    if adherence.get("low"):
        warnings.append(_("پایبندی ۳۰روزه %(percent)s٪ است و تعداد جلسه‌های هوازی برای اجراپذیری به %(effective)s کاهش یافت.") % {
            "percent": adherence.get("percent"),
            "effective": adherence.get("effective_sessions", sessions),
        })
    cardio_library_payload = [
        (
            item.pk,
            item.name,
            item.name_en,
            item.performance_type,
            tuple(sorted(required.pk for required in item.required_equipment_types.all())),
        )
        for item in CardioExercise.objects.prefetch_related("required_equipment_types").order_by("pk")
    ]
    cardio_library_fingerprint = hashlib.sha256(
        json.dumps(cardio_library_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "days": days,
        "correctives": [],
        "warnings": warnings,
        "metadata": {
            "algorithm_version": ALGORITHM_VERSION,
            "seed": seed_value,
            "athlete_id": profile.user_id if profile else None,
            "profile_updated_at": profile.updated_at.isoformat() if profile else None,
            "profile_goal": profile.primary_goal if profile else None,
            "program_goal": goal,
            "selected_training_goal": training_goal,
            "training_goal_source": training_goal_source or ("admin" if training_goal else "program_goal"),
            "session_duration_minutes": profile.session_duration_minutes if profile else None,
            "readiness": readiness,
            "adherence": adherence,
            "preferred_days": valid_days,
            "equipment_ids": sorted(equipment_ids),
            "eligible_exercise_count": len(cardio_exercises),
            "library_fingerprint": cardio_library_fingerprint,
            "human_review_required": not bool(profile and equipment_ids) or bool(readiness.get("low")),
        },
    }
