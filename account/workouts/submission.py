"""Read and validate fields posted by workout forms."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from django.utils.translation import gettext_lazy as _

from account.models import WorkoutPerformanceRecord
from account.services.training_periodization_service import TrainingPeriodizationService
from account.utils import normalize_digits

from .support import gym_program_service
from .program_context import build_performance_groups


def parse_performance_value(raw_value):
    normalized = normalize_digits(raw_value).strip()
    normalized = normalized.replace(",", ".").replace("٫", ".")
    if not re.fullmatch(r"\d+(?:\.\d{1,2})?", normalized):
        raise ValueError(_("مقدار باید یک عدد مثبت باشد."))
    try:
        value = Decimal(normalized)
    except InvalidOperation:
        raise ValueError(_("مقدار باید یک عدد مثبت باشد."))
    if value <= 0:
        raise ValueError(_("مقدار باید بیشتر از صفر باشد."))
    if value > Decimal("999999.99"):
        raise ValueError(_("مقدار واردشده بیش از حد مجاز است."))
    return value


def read_session_sets(data, items, program):
    session_sets = []
    movement_slots = (
        ("main", "exercise", "cardio_exercise", "reps", "sets"),
        ("superset", "superset_exercise", None, "superset_reps", "superset_sets"),
        ("third", "third_exercise", None, "third_reps", "third_sets"),
    )
    for item in items:
        for slot, exercise_field, cardio_field, reps_field, sets_field in movement_slots:
            movement = getattr(item, exercise_field) if exercise_field else None
            cardio = getattr(item, cardio_field) if cardio_field else None
            if slot == "main" and cardio is not None:
                movement = None
            if movement is None and cardio is None:
                continue
            planned_reps = getattr(item, reps_field, None) or item.reps
            planned_sets = getattr(item, sets_field, None) or item.sets
            planned_sets = TrainingPeriodizationService.active_sets(program, planned_sets)
            prefix = f"session_{item.pk}_{slot}"
            for set_number in range(1, gym_program_service.parse_set_count(planned_sets) + 1):
                field_prefix = f"{prefix}_{set_number}"
                raw_fields = {
                    "actual_reps": data.get(f"{field_prefix}_actual_reps"),
                    "actual_load_kg": data.get(f"{field_prefix}_actual_load_kg"),
                    "duration_seconds": data.get(f"{field_prefix}_duration_seconds"),
                    "distance_m": data.get(f"{field_prefix}_distance_m"),
                    "average_heart_rate_bpm": data.get(f"{field_prefix}_average_heart_rate_bpm"),
                    "peak_heart_rate_bpm": data.get(f"{field_prefix}_peak_heart_rate_bpm"),
                    "rpe": data.get(f"{field_prefix}_rpe"),
                    "rir": data.get(f"{field_prefix}_rir"),
                }
                has_outcome = any((value or "").strip() for value in raw_fields.values())
                attempted = data.get(f"{field_prefix}_attempted") == "1"
                completed = data.get(f"{field_prefix}_completed") == "1"
                if not has_outcome and not (attempted and not completed):
                    continue
                session_sets.append({
                    "program_exercise_id": item.pk,
                    "slot": slot,
                    "set_number": set_number,
                    **raw_fields,
                    "completed": completed,
                })
    return session_sets


def read_legacy_performance(data, user, items, program):
    valid_modes = set(WorkoutPerformanceRecord.Mode.values)
    performance_values = []
    original_modes = {
        item.pk: item.performance_modes
        if isinstance(item.performance_modes, dict)
        else {}
        for item in items
    }
    selected_modes_by_item = {
        item_id: dict(saved_modes)
        for item_id, saved_modes in original_modes.items()
    }
    errors: list[tuple[str | None, str]] = []

    for group in build_performance_groups(items, program=program):
        posted_sources = [
            (
                source,
                f"performance_{source['item'].pk}_{source['slot']}",
            )
            for source in group["sources"]
            if data.get(
                f"performance_{source['item'].pk}_{source['slot']}_mode"
            )
        ]
        if len(posted_sources) > 1:
            entries = [
                (
                    source,
                    prefix,
                    gym_program_service.parse_set_count(source["sets"]),
                )
                for source, prefix in posted_sources
            ]
        else:
            entries = [
                (
                    group["sources"][0],
                    group["input_prefix"],
                    group["set_count"],
                )
            ]

        for source, prefix, set_count in entries:
            mode = (data.get(f"{prefix}_mode") or "").strip()
            if not mode:
                continue
            if mode not in valid_modes:
                errors.append((None, _("نحوه ثبت عملکرد یکی از حرکت‌ها معتبر نیست.")))
                continue

            for selected_source in (
                group["sources"] if len(posted_sources) <= 1 else [source]
            ):
                selected_modes_by_item[selected_source["item"].pk][
                    selected_source["slot"]
                ] = mode

            for set_number in range(1, set_count + 1):
                raw_value = data.get(
                    f"{prefix}_{mode}_set_{set_number}"
                )
                if not (raw_value or "").strip():
                    continue
                try:
                    value = parse_performance_value(raw_value)
                except ValueError as exc:
                    errors.append(
                        (
                            f"{prefix}_{mode}_set_{set_number}",
                            _("%(exercise)s، ست %(set)s: %(error)s")
                            % {
                                "exercise": group["exercise"].name,
                                "set": set_number,
                                "error": str(exc),
                            },
                        )
                    )
                    continue
                performance_values.append(
                    {
                        "user": user,
                        "exercise": group["exercise"],
                        "program": program,
                        "program_exercise": source["item"],
                        "repetitions": normalize_digits(group["repetitions"]).strip(),
                        "mode": mode,
                        "set_number": set_number,
                        "value": value,
                    }
                )

    mode_selections = {
        item_id: selected_modes
        for item_id, selected_modes in selected_modes_by_item.items()
        if selected_modes != original_modes[item_id]
    }
    return performance_values, mode_selections, errors
