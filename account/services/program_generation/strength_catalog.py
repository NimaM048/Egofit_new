"""Filter the strength exercise catalog and retain candidate pools."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from account.models import Exercise, ExerciseMovementType

from .catalog import (
    _allowed_difficulty_level_ids,
    _secondary_movement_types,
    _selected_movement_type_ids,
    _selected_pattern_ids,
)


@dataclass(frozen=True)
class StrengthCatalog:
    """Exercises eligible for each requested target and the relevant exclusions."""

    difficulty_id: int
    candidates: list[Exercise]
    exercises: list[Exercise]
    by_target: dict[tuple[str, int], list[Exercise]]
    preferred_by_target: dict[tuple[str, int], list[Exercise]]
    fallback_by_target: dict[tuple[str, int], list[Exercise]]
    excluded_targets: dict[tuple[str, int], set[str]]
    excluded_by_equipment: int
    excluded_by_restriction: int


def build_target_maps(secondary_movement_counts, session_movement_targets):
    selected_ids = _selected_movement_type_ids(
        secondary_movement_counts,
        session_movement_targets,
    )
    selected_pattern_ids = _selected_pattern_ids(session_movement_targets)
    movement_type_map = {
        item.pk: item for item in _secondary_movement_types(selected_ids)
    }
    pattern_type_map = {
        item.pk: item
        for item in ExerciseMovementType.objects.filter(pk__in=selected_pattern_ids)
    }
    selected_ids = [pk for pk in selected_ids if pk in movement_type_map]
    selected_pattern_ids = [pk for pk in selected_pattern_ids if pk in pattern_type_map]
    if not selected_ids and not selected_pattern_ids:
        raise ValidationError(_("برای جلسه‌ها بخش عضله یا الگوی حرکت انتخاب نشده است."))
    return selected_ids, selected_pattern_ids, movement_type_map, pattern_type_map


def load_strength_catalog(
    *,
    difficulty,
    abnormalities,
    selected_equipment_ids,
    selected_ids,
    selected_pattern_ids,
    filter_by_equipment,
    preferences,
):
    difficulty_id = getattr(difficulty, "pk", difficulty)
    allowed_difficulty_ids = _allowed_difficulty_level_ids(difficulty)
    exercise_filter = Q()
    if selected_ids:
        exercise_filter |= Q(secondary_movement_type_id__in=selected_ids)
    if selected_pattern_ids:
        exercise_filter |= Q(movement_type_id__in=selected_pattern_ids)
    queryset = Exercise.objects.select_related(
        "primary_muscle",
        "secondary_muscle",
        "equipment_type",
        "body_part",
        "joint_type",
        "power_type",
        "difficulty_level",
        "secondary_movement_type",
        "movement_type",
    ).prefetch_related("required_equipment_types", "excluded_abnormalities").filter(
        exercise_filter,
        difficulty_level_id__in=allowed_difficulty_ids,
    )
    candidate_rows = list(queryset)
    exercises = []
    excluded_by_equipment = 0
    excluded_by_restriction = 0
    excluded_targets = defaultdict(set)
    excluded_target_signatures = defaultdict(set)
    abnormality_ids = {item.pk for item in abnormalities}
    for exercise in candidate_rows:
        target_keys = []
        if exercise.secondary_movement_type_id:
            target_keys.append(("section", exercise.secondary_movement_type_id))
        if exercise.movement_type_id:
            target_keys.append(("pattern", exercise.movement_type_id))
        signature = (exercise.primary_muscle_id, exercise.movement_type_id)
        required_equipment = {item.pk for item in exercise.required_equipment_types.all()}
        if not required_equipment and exercise.equipment_type_id:
            required_equipment = {exercise.equipment_type_id}
        if filter_by_equipment and required_equipment - selected_equipment_ids:
            excluded_by_equipment += 1
            for key in target_keys:
                excluded_targets[key].add("equipment")
                if all(signature):
                    excluded_target_signatures[key].add(signature)
            continue
        if exercise.pk in preferences["pain"]:
            excluded_by_restriction += 1
            for key in target_keys:
                excluded_targets[key].add("pain")
                if all(signature):
                    excluded_target_signatures[key].add(signature)
            continue
        blocked_ids = {item.pk for item in exercise.excluded_abnormalities.all()}
        if blocked_ids.intersection(abnormality_ids):
            excluded_by_restriction += 1
            for key in target_keys:
                excluded_targets[key].add("restriction")
                if all(signature):
                    excluded_target_signatures[key].add(signature)
            continue
        exercises.append(exercise)

    # If every exercise in a requested section is filtered, allow a strict
    # substitute only when it trains the same primary muscle through the
    # same movement pattern and still passes all equipment/health filters.
    fallback_by_target = defaultdict(list)
    fallback_query = Q()
    for (target_kind, _target_id), signatures in excluded_target_signatures.items():
        if target_kind == "section":
            for muscle_id, pattern_id in signatures:
                fallback_query |= Q(primary_muscle_id=muscle_id, movement_type_id=pattern_id)
    if fallback_query:
        fallback_rows = Exercise.objects.select_related(
            "primary_muscle", "body_part", "joint_type", "power_type", "equipment_type",
            "difficulty_level", "secondary_movement_type", "movement_type",
        ).prefetch_related("required_equipment_types", "excluded_abnormalities").filter(
            fallback_query,
            difficulty_level_id__in=allowed_difficulty_ids,
        )
        eligible_fallbacks = []
        for exercise in fallback_rows:
            required_equipment = {item.pk for item in exercise.required_equipment_types.all()}
            if not required_equipment and exercise.equipment_type_id:
                required_equipment = {exercise.equipment_type_id}
            if filter_by_equipment and required_equipment - selected_equipment_ids:
                continue
            if exercise.pk in preferences["pain"]:
                continue
            if {item.pk for item in exercise.excluded_abnormalities.all()}.intersection(abnormality_ids):
                continue
            eligible_fallbacks.append(exercise)
        for target_key, signatures in excluded_target_signatures.items():
            if target_key[0] != "section":
                continue
            for exercise in eligible_fallbacks:
                signature = (exercise.primary_muscle_id, exercise.movement_type_id)
                if signature in signatures and exercise.secondary_movement_type_id != target_key[1]:
                    fallback_by_target[target_key].append(exercise)
        exercises.extend(
            exercise for exercise in eligible_fallbacks
            if exercise.pk not in {item.pk for item in exercises}
        )
    by_target = defaultdict(list)
    preferred_by_target = defaultdict(list)
    for exercise in exercises:
        if exercise.secondary_movement_type_id:
            key = ("section", exercise.secondary_movement_type_id)
            by_target[key].append(exercise)
            if exercise.difficulty_level_id == difficulty_id:
                preferred_by_target[key].append(exercise)
        if exercise.movement_type_id:
            key = ("pattern", exercise.movement_type_id)
            by_target[key].append(exercise)
            if exercise.difficulty_level_id == difficulty_id:
                preferred_by_target[key].append(exercise)
    return StrengthCatalog(
        difficulty_id=difficulty_id,
        candidates=candidate_rows,
        exercises=exercises,
        by_target=by_target,
        preferred_by_target=preferred_by_target,
        fallback_by_target=fallback_by_target,
        excluded_targets=excluded_targets,
        excluded_by_equipment=excluded_by_equipment,
        excluded_by_restriction=excluded_by_restriction,
    )
