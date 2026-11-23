"""Fingerprint preview inputs and decode previously generated form payloads."""

from __future__ import annotations

import json
import hashlib

from account.models import CardioExercise, Exercise, MuscleVolumeLandmark, AthleteTrainingProfile
from account.services.training_adherence_service import TrainingAdherenceService
from account.services.program_generation import ALGORITHM_VERSION
from account.services.program_generation.catalog import (
    program_goal_for_training_goal,
    _is_cardio_goal,
    _library_fingerprint,
)
from account.services.program_generation.athlete import (
    _readiness_snapshot,
    _muscle_fatigue_snapshot,
    _exercise_preferences,
    _last_performance_snapshot,
    _approved_progression_loads,
    _approved_deload_recommendations,
    _approved_rep_progressions,
)


def settings_signature(cleaned_data, *, equipment_ids=None):
    athlete = cleaned_data["user"]
    profile = AthleteTrainingProfile.objects.filter(user=athlete).prefetch_related("available_equipment").first()
    effective_equipment_ids = (
        sorted(profile.available_equipment.values_list("pk", flat=True))
        if equipment_ids is None and profile
        else sorted(equipment_ids or [])
    )
    requested_training_goal = cleaned_data.get("athlete_goal") or None
    selected_training_goal = requested_training_goal or (profile.primary_goal if profile else None)
    effective_goal = cleaned_data["goal"]
    mapped_training_goal = program_goal_for_training_goal(selected_training_goal)
    if mapped_training_goal and not _is_cardio_goal(effective_goal):
        effective_goal = mapped_training_goal
    profile_data = None
    if profile:
        profile_data = {
            "updated_at": profile.updated_at.isoformat(),
            "goal": profile.primary_goal,
            "sessions": profile.sessions_per_week,
            "experience": profile.experience_years,
            "session_duration_minutes": profile.session_duration_minutes,
            "preferred_days": profile.preferred_days,
            "equipment": sorted(profile.available_equipment.values_list("pk", flat=True)),
        }
    readiness = _readiness_snapshot(athlete.pk)
    adherence = TrainingAdherenceService.snapshot(athlete)
    muscle_fatigue = _muscle_fatigue_snapshot(
        athlete.pk,
        profile.experience_years if profile else None,
    )
    preferences = _exercise_preferences(athlete.pk)
    last_performance = _last_performance_snapshot(athlete.pk)
    template = cleaned_data.get("template")
    exercises = list(
        Exercise.objects.prefetch_related("required_equipment_types", "excluded_abnormalities")
        .select_related("difficulty_level", "movement_type", "secondary_movement_type", "equipment_type")
        .order_by("pk")
    )
    cardio_catalog = list(CardioExercise.objects.prefetch_related("required_equipment_types").order_by("pk"))
    payload = {
        "algorithm_version": ALGORITHM_VERSION,
        "user": athlete.pk,
        "program_type": cleaned_data.get("program_type") or "normal",
        "training_methods": sorted(cleaned_data.get("training_methods") or []),
        "difficulty": cleaned_data["difficulty"].pk,
        "abnormalities": sorted(item.pk for item in cleaned_data.get("abnormalities", [])),
        "sessions": cleaned_data["sessions_per_week"],
        "movements": cleaned_data["movements_per_session"],
        "goal": effective_goal,
        "requested_training_goal": requested_training_goal,
        "selected_training_goal": selected_training_goal,
        "seed": cleaned_data.get("seed") or "",
        "equipment_selection": effective_equipment_ids,
        "selected_targets": sorted(item.pk for item in cleaned_data.get("target_secondary_movement_types", [])),
        "movement_counts": cleaned_data.get("secondary_movement_counts_json") or {},
        "session_targets": cleaned_data.get("session_movement_targets_json") or [],
        "profile": profile_data,
        "readiness": readiness,
        "adherence": adherence,
        "muscle_fatigue": muscle_fatigue,
        "exercise_preferences": {key: sorted(value) for key, value in preferences.items()},
        "last_performance": last_performance,
        "approved_progression": sorted(
            (key, str(item.reviewed_load_kg), item.reviewed_at.isoformat() if item.reviewed_at else "")
            for key, item in _approved_progression_loads(athlete.pk).items()
        ),
        "approved_deloads": sorted(
            (key, item.pk)
            for key, item in _approved_deload_recommendations(athlete.pk).items()
        ),
        "approved_rep_progressions": sorted(
            (key, item.pk)
            for key, item in _approved_rep_progressions(athlete.pk).items()
        ),
        "volume_landmarks": list(
            MuscleVolumeLandmark.objects.order_by("pk").values_list(
                "pk", "mev", "mav", "mrv", "updated_at"
            )
        ),
        "template": {
            "id": template.pk,
            "updated_at": template.updated_at.isoformat(),
            "payload": template.payload,
            "weeks": template.weeks,
            "training_goal": template.training_goal,
            "difficulty": template.difficulty_id,
            "days_per_week": template.days_per_week,
        } if template else None,
        "exercise_library": _library_fingerprint(exercises),
        "cardio_library": [
            (item.pk, item.name, item.name_en, item.performance_type, sorted(required.pk for required in item.required_equipment_types.all()))
            for item in cardio_catalog
        ],
    }
    normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def decode_preview(days_json, correctives_json, generation_metadata_json=None, preview_token=None):
    try:
        days = json.loads(days_json or "[]")
        correctives = json.loads(correctives_json or "[]")
        metadata = json.loads(generation_metadata_json or "{}")
        return {
            "days": days if isinstance(days, list) else [],
            "correctives": correctives if isinstance(correctives, list) else [],
            "metadata": metadata if isinstance(metadata, dict) else {},
            "token": str(preview_token or ""),
        }
    except (TypeError, ValueError):
        return {"days": [], "correctives": [], "metadata": {}, "token": ""}
