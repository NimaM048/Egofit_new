"""Attach observed performance data and movement rows to workout previews."""

from __future__ import annotations

import re
from collections import defaultdict

from django.utils.translation import gettext_lazy as _

from account.models import WorkoutPerformanceRecord, WorkoutBestRecord
from account.services.training_periodization_service import TrainingPeriodizationService
from account.utils import normalize_digits

from .support import gym_program_service


def normalize_repetition_key(repetitions):
    normalized = normalize_digits(repetitions or "").strip()
    parts = re.findall(r"\d+", normalized)
    if not parts:
        return normalized
    return "-".join(parts)


def build_performance_groups(items, *, program=None):
    groups = {}
    for item in items:
        movement_slots = (
            ("main", item.exercise, item.sets, item.reps, item.rest),
            (
                "superset",
                item.superset_exercise,
                item.superset_sets if item.superset_sets is not None else item.sets,
                item.superset_reps if item.superset_reps is not None else item.reps,
                item.superset_rest if item.superset_rest is not None else item.rest,
            ),
            (
                "third",
                item.third_exercise,
                item.third_sets if item.third_sets is not None else item.sets,
                item.third_reps if item.third_reps is not None else item.reps,
                item.third_rest if item.third_rest is not None else item.rest,
            ),
        )
        for slot, exercise, sets, reps, rest in movement_slots:
            if program:
                sets = TrainingPeriodizationService.active_sets(program, sets)
            if exercise is None:
                continue
            repetitions = str(reps or "").strip()
            key = (
                exercise.pk,
                normalize_repetition_key(
                    repetitions
                ),
            )
            group = groups.setdefault(
                key,
                {
                    "exercise": exercise,
                    "repetitions": repetitions,
                    "set_count": 1,
                    "sources": [],
                    "input_prefix": f"performance_{item.pk}_{slot}",
                },
            )
            _set_min, set_count = gym_program_service._range_from_text(sets)
            group["set_count"] = max(group["set_count"], set_count or gym_program_service.parse_set_count(sets))
            group["sources"].append(
                {
                    "item": item,
                    "slot": slot,
                    "sets": sets,
                    "reps": repetitions,
                    "rest": rest,
                }
            )
    return list(groups.values())


def uses_bodyweight_pressure(exercise):
    pressure_type = getattr(exercise, "pressure_type", None)
    if pressure_type is None:
        return False
    name = re.sub(r"\s+", "", str(pressure_type.name or "").casefold()).replace("／", "/")
    return name in {
        "زمان",
        "وزنبدن",
        "وزنبدن/زمان",
        "time",
        "bodyweight",
        "bodyweight/time",
    }


def attach_performance_context(program):
    days = list(program.days.all())
    groups_by_day = {
        day.pk: build_performance_groups(
            list(day.items.all()), program=program
        )
        for day in days
    }
    exercise_ids = {
        group["exercise"].pk
        for groups in groups_by_day.values()
        for group in groups
    }
    records = list(
        WorkoutPerformanceRecord.objects.filter(
            user=program.user,
            exercise_id__in=exercise_ids,
        )
    )
    records_by_movement = defaultdict(list)
    for record in records:
        records_by_movement[
            (
                record.exercise_id,
                normalize_repetition_key(
                    record.repetitions
                ),
            )
        ].append(record)

    mode_units = {
        WorkoutPerformanceRecord.Mode.WEIGHT: _("کیلوگرم"),
        WorkoutPerformanceRecord.Mode.BODY_WEIGHT: _("کیلوگرم"),
        WorkoutPerformanceRecord.Mode.TIME: _("ثانیه"),
    }
    mode_choices = WorkoutPerformanceRecord.Mode.choices
    mode_values = {mode_value for mode_value, _mode_label in mode_choices}
    for day in days:
        for item in day.items.all():
            item.performance_movements = []

    for day in days:
        day.performance_movements = []
        for group in groups_by_day[day.pk]:
            exercise = group["exercise"]
            repetitions = group["repetitions"]
            movement_records = records_by_movement.get(
                (
                    exercise.pk,
                    normalize_repetition_key(
                        repetitions
                    ),
                ),
                [],
            )
            latest_record = max(
                movement_records,
                key=lambda record: record.updated_at,
                default=None,
            )
            previous_record = max(
                (
                    record
                    for record in movement_records
                    if record.program_id != program.pk
                ),
                key=lambda record: record.updated_at,
                default=None,
            )
            modes = []
            for mode_value, mode_label in mode_choices:
                mode_records = [
                    record
                    for record in movement_records
                    if record.mode == mode_value
                ]
                values_by_set = {
                    record.set_number: record.value
                    for record in mode_records
                }
                best_value = max(
                    (record.value for record in mode_records),
                    default=None,
                )

                modes.append(
                    {
                        "value": mode_value,
                        "label": mode_label,
                        "unit": mode_units[mode_value],
                        "step": "1" if mode_value == WorkoutPerformanceRecord.Mode.TIME else "0.01",
                        "best": best_value,
                        "sets": [
                            {
                                "number": set_number,
                                "value": values_by_set.get(set_number),
                            }
                            for set_number in range(1, group["set_count"] + 1)
                        ],
                    }
            )
            saved_mode = next(
                (
                    source["item"].performance_modes.get(source["slot"])
                    for source in group["sources"]
                    if isinstance(source["item"].performance_modes, dict)
                    and source["item"].performance_modes.get(source["slot"]) in mode_values
                ),
                None,
            )
            movement_context = {
                "exercise": exercise,
                "slot": group["sources"][0]["slot"],
                "input_prefix": group["input_prefix"],
                "selected_mode": (
                    saved_mode
                    or (
                        latest_record.mode
                        if latest_record
                        else WorkoutPerformanceRecord.Mode.WEIGHT
                    )
                ),
                "modes": modes,
                "repetitions": repetitions,
                "source_count": len(group["sources"]),
                "previous_record": previous_record,
            }
            day.performance_movements.append(movement_context)
            for source in group["sources"]:
                source_saved_modes = source["item"].performance_modes
                source_selected_mode = (
                    source_saved_modes.get(source["slot"])
                    if isinstance(source_saved_modes, dict)
                    and source_saved_modes.get(source["slot"]) in mode_values
                    else movement_context["selected_mode"]
                )
                _source_set_min, source_set_count = gym_program_service._range_from_text(source["sets"])
                source_set_count = source_set_count or gym_program_service.parse_set_count(source["sets"])
                source_modes = [
                    {
                        **mode,
                        "sets": mode["sets"][:source_set_count],
                    }
                    for mode in movement_context["modes"]
                ]
                source["item"].performance_movements.append(
                    {
                        **movement_context,
                        "slot": source["slot"],
                        "input_prefix": (
                            f"performance_{source['item'].pk}_{source['slot']}"
                        ),
                        "selected_mode": source_selected_mode,
                        "modes": source_modes,
                    }
                )


def attach_best_record_context(program):
    days = list(program.days.all())
    exercise_ids = {
        exercise.pk
        for day in days
        for item in day.items.all()
        for exercise in (item.exercise, item.superset_exercise, item.third_exercise)
        if exercise is not None
    }
    records = {
        record.exercise_id: record
        for record in WorkoutBestRecord.objects.filter(
            user=program.user,
            exercise_id__in=exercise_ids,
        )
    }
    for day in days:
        rows = []
        for item in day.items.all():
            movement_slots = (
                (
                    "main", item.movement,
                    TrainingPeriodizationService.active_sets(program, item.sets),
                    item.reps, item.rest, "",
                ),
                (
                    "superset",
                    item.superset_exercise,
                    TrainingPeriodizationService.active_sets(
                        program,
                        item.superset_sets if item.superset_sets is not None else item.sets,
                    ),
                    item.superset_reps if item.superset_reps is not None else item.reps,
                    item.superset_rest if item.superset_rest is not None else item.rest,
                    _("سوپرست با حرکت بالا"),
                ),
                (
                    "third",
                    item.third_exercise,
                    TrainingPeriodizationService.active_sets(
                        program,
                        item.third_sets if item.third_sets is not None else item.sets,
                    ),
                    item.third_reps if item.third_reps is not None else item.reps,
                    item.third_rest if item.third_rest is not None else item.rest,
                    _("حرکت سوم برای دو حرکت بالا"),
                ),
            )
            for slot, exercise, sets, reps, rest, label in movement_slots:
                if exercise is None:
                    continue
                rows.append(
                    {
                        "item": item,
                        "slot": slot,
                        "exercise": exercise,
                        "is_cardio": slot == "main" and bool(item.cardio_exercise_id),
                        "sets": sets,
                        "reps": reps,
                        "rest": rest,
                        "label": label,
                        "record": records.get(exercise.pk) if not (slot == "main" and item.cardio_exercise_id) else None,
                        "input_name": f"best_record_{exercise.pk}" if not (slot == "main" and item.cardio_exercise_id) else "",
                        "set_count": gym_program_service.parse_set_count(sets),
                        "set_numbers": range(1, gym_program_service.parse_set_count(sets) + 1),
                        "session_prefix": f"session_{item.pk}_{slot}",
                        "default_load_kg": (
                            program.user.weight_kg
                            if not (slot == "main" and item.cardio_exercise_id)
                            and uses_bodyweight_pressure(exercise)
                            else None
                        ),
                    }
                )
        day.workout_rows = rows
