"""Parsing and validation helpers for exercise-library prescriptions."""

from __future__ import annotations

import re


_PYRAMID = "pyramid"
_REVERSE_PYRAMID = "reverse_pyramid"
_TWENTY_ONE_REPS = "21_reps"
_DROP_SET = "drop_set"
_LOW_TO_HIGH = "low_to_high"
_HIGH_TO_LOW = "high_to_low"


_DIGIT_MAP = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_OPTIONAL_SET_UNIT = r"(?:\s*(?:sets?|ست))?"
_OPTIONAL_REP_UNIT = r"(?:\s*(?:reps?|تکرار))?"
_RANGE_SEPARATOR = r"(?:-|–|—|تا|to)"
_CARDIO_DURATION_UNIT = r"(?:seconds?|secs?|s|minutes?|mins?|min|m|ثانیه|دقیقه)"
_CARDIO_GOALS = {"cardio", "aerobic", "هوازی"}


def parse_set_prescription(value: object) -> tuple[int, int] | None:
    """Return the inclusive minimum/maximum for a set count such as ``۳`` or ``2-3``."""
    text = str(value or "").translate(_DIGIT_MAP).strip().casefold()
    match = re.fullmatch(
        rf"(\d+)(?:\s*{_RANGE_SEPARATOR}\s*(\d+))?{_OPTIONAL_SET_UNIT}",
        text,
    )
    if not match:
        return None
    minimum = int(match.group(1))
    maximum = int(match.group(2) or minimum)
    if minimum < 1 or maximum < minimum:
        return None
    return minimum, maximum


def _method_rep_sequence(value: object, *, separators: str) -> list[int] | None:
    """Parse a positive-number sequence using only the supplied separators."""
    text = str(value or "").translate(_DIGIT_MAP).strip()
    separator_pattern = f"[{re.escape(separators)}]"
    if not re.fullmatch(rf"\d+(?:\s*{separator_pattern}\s*\d+)+", text):
        return None
    numbers = [int(part.strip()) for part in re.split(rf"\s*{separator_pattern}\s*", text)]
    return numbers if all(number > 0 for number in numbers) else None


def _drop_set_sequence(value: object) -> list[int] | None:
    """Parse a two- or three-value drop-set sequence separated by < or >."""
    text = str(value or "").translate(_DIGIT_MAP).strip()
    if not re.fullmatch(r"\d+(?:\s*[<>]\s*\d+){1,2}", text):
        return None
    numbers = [int(part.strip()) for part in re.split(r"\s*[<>]\s*", text)]
    return numbers if all(number > 0 for number in numbers) else None


def _is_simple_rep_range(value: object) -> bool:
    text = str(value or "").translate(_DIGIT_MAP).strip().casefold()
    match = re.fullmatch(
        rf"(\d+)(?:\s*{_RANGE_SEPARATOR}\s*(\d+))?{_OPTIONAL_REP_UNIT}",
        text,
    )
    if not match:
        return False
    minimum = int(match.group(1))
    maximum = int(match.group(2) or minimum)
    return minimum > 0 and maximum >= minimum


def _is_cardio_duration(value: object) -> bool:
    text = str(value or "").translate(_DIGIT_MAP).strip().casefold()
    match = re.fullmatch(
        rf"(\d+)(?:\s*{_RANGE_SEPARATOR}\s*(\d+))?\s*{_CARDIO_DURATION_UNIT}",
        text,
    )
    if not match:
        return False
    minimum = int(match.group(1))
    maximum = int(match.group(2) or minimum)
    return minimum > 0 and maximum >= minimum


def is_valid_repetition_prescription(value: object, method: str, goal: str = "") -> bool:
    """Validate a rep range, cardio duration, or method-specific rep sequence."""
    normalized_goal = " ".join(str(goal or "").casefold().replace("_", " ").split())
    if method == "normal":
        if normalized_goal in _CARDIO_GOALS and _is_cardio_duration(value):
            return True
        return _is_simple_rep_range(value)
    if method == _TWENTY_ONE_REPS:
        return _method_rep_sequence(value, separators="+") == [7, 7, 7]
    if method == _DROP_SET:
        sequence = _drop_set_sequence(value)
        return sequence is not None and 2 <= len(sequence) <= 3
    if method in {_PYRAMID, _REVERSE_PYRAMID}:
        sequence = _method_rep_sequence(value, separators=".+")
        return sequence is not None and 4 <= len(sequence) <= 10
    if method in {_LOW_TO_HIGH, _HIGH_TO_LOW}:
        sequence = _method_rep_sequence(value, separators="+")
        if sequence is None or not 3 <= len(sequence) <= 10:
            return False
        descending = method == _LOW_TO_HIGH
        compare = (lambda first, second: first >= second) if descending else (lambda first, second: first <= second)
        return all(compare(first, second) for first, second in zip(sequence, sequence[1:]))
    return _is_simple_rep_range(value)
