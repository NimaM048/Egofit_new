"""Load and apply set, repetition, and rest prescriptions."""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from account.domain.exercise_goals import goal_lookup_aliases
from account.domain.workout_prescriptions import (
    is_valid_repetition_prescription,
    parse_set_prescription,
)
from account.models import (
    ExerciseSetType,
    ExerciseRepetitionType,
    ExerciseRestType,
    ExerciseTrainingMethod,
)
from account.utils import normalize_digits


SET_MATCHED_REPETITION_METHODS = frozenset({
    ExerciseTrainingMethod.PYRAMID,
    ExerciseTrainingMethod.REVERSE_PYRAMID,
    ExerciseTrainingMethod.LOW_TO_HIGH,
    ExerciseTrainingMethod.HIGH_TO_LOW,
})


METHOD_NOTES = {
    ExerciseTrainingMethod.PYRAMID: "هر ست وزنه سنگین و سپس برعکس رفتار کنید.",
    ExerciseTrainingMethod.REVERSE_PYRAMID: "هر ست وزنه سبک به سنگین پیش برود و سپس برعکس رفتار کنید.",
    ExerciseTrainingMethod.TWENTY_ONE_REPS: "21 Reps: ۷ تکرار نیمه پایین + ۷ تکرار نیمه بالا + ۷ تکرار کامل.",
    ExerciseTrainingMethod.DROP_SET: "هر ست اول تکرار کمتر و بعد با سبک کردن وزنه تکرار بعدی بلافاصله اجرا شود.",
    ExerciseTrainingMethod.LOW_TO_HIGH: "Low To High: بار را از سبک به سنگین افزایش دهید؛ تکرارها نزولی باشند.",
    ExerciseTrainingMethod.HIGH_TO_LOW: "High To Low: بار را از سنگین به سبک کاهش دهید؛ تکرارها صعودی باشند.",
}


def _set_count(value: Any) -> tuple[int, int]:
    """Return a safe weekly-volume estimate for an exact or ranged set count."""
    return parse_set_prescription(value) or (0, 0)


def _format_set_range(minimum: int, maximum: int) -> str:
    return str(minimum) if minimum == maximum else f"{minimum}–{maximum}"


def _prescription_catalogs(goal: str, method: str) -> dict[str, list[str]]:
    catalogs = {
        "sets": _ordered_catalog(
            _lookup_values(ExerciseSetType, "set_count", goal, method)
        ),
        "reps": _ordered_catalog(
            _lookup_values(ExerciseRepetitionType, "reps", goal, method)
        ),
        "rest": _ordered_catalog(
            _lookup_values(ExerciseRestType, "rest_time", goal, method)
        ),
    }
    if method in {
        ExerciseTrainingMethod.PYRAMID,
        ExerciseTrainingMethod.REVERSE_PYRAMID,
        ExerciseTrainingMethod.TWENTY_ONE_REPS,
        ExerciseTrainingMethod.LOW_TO_HIGH,
        ExerciseTrainingMethod.HIGH_TO_LOW,
    }:
        catalogs["reps"] = [
            value
            for value in catalogs["reps"]
            if _valid_repetition_sequence(value, method)
        ]
    if any(not values for values in catalogs.values()):
        method_label = dict(ExerciseTrainingMethod.choices).get(method, method)
        raise ValidationError(
            _("برای هدف «%(goal)s» و روش «%(method)s» باید ست، تکرار و استراحت در کتابخانه ثبت شود.")
            % {"goal": goal, "method": method_label}
        )
    return catalogs


def _valid_repetition_sequence(value: str, method: str) -> bool:
    return is_valid_repetition_prescription(value, method)


def _ordered_catalog(values: Sequence[str]) -> list[str]:
    """Keep unique catalog values in priority order for repeatable variation."""
    return list(dict.fromkeys(value for value in values if value))


def _prescription_for_index(
    catalogs: Mapping[str, Sequence[str]],
    index: int,
    previous: Mapping[str, str] | None = None,
    method: str = ExerciseTrainingMethod.NORMAL,
) -> dict[str, str]:
    """Choose a goal-specific prescription with set-matched rep sequences."""
    previous = previous or {}
    prescription = {}

    set_values = list(catalogs.get("sets", []))
    repetition_values = list(catalogs.get("reps", []))
    if set_values and repetition_values:
        compatible_sets = [
            (set_value, _repetitions_compatible_with_sets(repetition_values, set_value, method))
            for set_value in set_values
        ]
        compatible_sets = [entry for entry in compatible_sets if entry[1]]
        if not compatible_sets:
            method_label = dict(ExerciseTrainingMethod.choices).get(method, method)
            raise ValidationError(
                _(
                    "برای روش «%(method)s» در کتابخانه، نسخهٔ تکراری هم‌اندازه با تعداد ست‌های موجود ثبت نشده است."
                ) % {"method": method_label}
            )

        set_index = index % len(compatible_sets)
        if (
            compatible_sets[set_index][0] == previous.get("sets")
            and len(compatible_sets) > 1
        ):
            set_index = (set_index + 1) % len(compatible_sets)
        selected_set, compatible_repetitions = compatible_sets[set_index]

        repetition_index = index % len(compatible_repetitions)
        if (
            compatible_repetitions[repetition_index] == previous.get("reps")
            and len(compatible_repetitions) > 1
        ):
            repetition_index = (repetition_index + 1) % len(compatible_repetitions)
        prescription["sets"] = selected_set
        prescription["reps"] = compatible_repetitions[repetition_index]

    for field, values in catalogs.items():
        if field in {"sets", "reps"} or not values:
            continue
        selected = values[index % len(values)]
        if selected == previous.get(field) and len(values) > 1:
            selected = values[(index + 1) % len(values)]
        prescription[field] = selected
    return prescription


def _repetitions_compatible_with_sets(
    repetition_values: Sequence[str],
    set_value: str,
    method: str,
) -> list[str]:
    """Match sequence-based prescriptions to the number of selected sets."""
    if method not in SET_MATCHED_REPETITION_METHODS:
        return list(repetition_values)

    minimum_sets, maximum_sets = _set_count(set_value)
    if minimum_sets < 1:
        # Preserve older, custom lookup labels that are not numeric counts.
        return list(repetition_values)

    return [
        value
        for value in repetition_values
        if minimum_sets
        <= len(re.findall(r"\d+", normalize_digits(value)))
        <= maximum_sets
    ]


def _lookup_values(
    model: Any,
    field: str,
    goal: str,
    method: str = ExerciseTrainingMethod.NORMAL,
) -> list[str]:
    """Load catalog values matching the requested goal and training method."""
    aliases = goal_lookup_aliases(goal)
    value_records = []
    for alias in aliases:
        value_records.extend(
            (item.pk, getattr(item, field))
            for item in model.objects.filter(goal__iexact=alias, method=method).order_by("-pk")
        )

    # General programs commonly use the intentionally blank catalog goal.
    if not value_records and goal == "general":
        value_records.extend(
            (item.pk, getattr(item, field))
            for item in model.objects.filter(goal="", method=method).order_by("pk")
        )
    value_records.sort(key=lambda entry: entry[0], reverse=True)
    return list(dict.fromkeys(value for _pk, value in value_records if value))


def _lookup_value(model: Any, field: str, goal: str, fallback: str) -> str:
    """Resolve the first goal-specific prescription text with a fallback."""
    values = _lookup_values(model, field, goal)
    return values[0] if values else fallback
