"""Exercise selection, target ordering, and session plan rules."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

from django.core.exceptions import ValidationError
from django.db.models import QuerySet
from django.utils.translation import gettext_lazy as _

from account.domain.exercise_goals import GOAL_LOOKUP_ALIASES, canonical_goal_key
from account.models import (
    Exercise,
    ExerciseDifficultyLevel,
    ExerciseMovementType,
    ExerciseSecondaryMovementType,
    AthleteTrainingProfile,
)


DIFFICULTY_ALIASES: dict[int, tuple[str, ...]] = {
    0: (
        "beginner",
        "novice",
        "basic",
        "مبتدی",
        "ابتدایی",
        "آسان",
    ),
    1: (
        "intermediate",
        "medium",
        "moderate",
        "متوسط",
        "میانی",
    ),
    2: (
        "advanced",
        "difficult",
        "hard",
        "expert",
        "پیشرفته",
        "دشوار",
        "سخت",
    ),
}


GOAL_ALIASES: dict[str, tuple[str, ...]] = GOAL_LOOKUP_ALIASES


POWER_TYPE_ALIASES = ("power", "توان", "توانی", "explosive")


COMPOUND_JOINT_ALIASES = (
    "compound",
    "multi-joint",
    "multijoint",
    "چند مفصلی",
    "چندمفصلی",
)


SECTION_ALIASES = {
    "chest": ("chest", "سینه"),
    "shoulders": ("shoulders", "shoulder", "سرشانه", "شانه"),
    "triceps": ("triceps", "tricep", "پشت بازو"),
    "legs": ("legs", "leg", "پا"),
    "glutes": ("glutes", "glute", "سرینی", "باسن"),
    "calves": ("calves", "calf", "ساق"),
    "abs": ("abs", "abdomen", "abdominal", "شکم"),
    "lower_back": ("lower back", "بخش پایینی پشت"),
    "lumbar": ("lumbar", "فیله", "کمر", "راست‌کننده ستون فقرات"),
    "upper_back": ("upper back", "بخش بالایی پشت"),
    "biceps": ("biceps", "bicep", "جلو بازو"),
}


FULL_BODY_SECTION_ORDER = (
    "legs",
    "glutes",
    "calves",
    "chest",
    "upper_back",
    "shoulders",
    "lower_back",
    "biceps",
    "triceps",
    "lumbar",
    "abs",
)


PATTERN_ALIASES = {
    "push": ("push", "هل دادن", "هل‌دادن"),
    "pull": ("pull", "کشیدن"),
    "legs": ("legs", "leg", "پا"),
    "core": ("core", "central", "مرکزی بدن"),
}


def build_session_targets(
    *,
    program_type: str,
    sessions: int,
    current_targets: Sequence[dict[str, Any]] | None = None,
    selected_ids: Sequence[int] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    session_count = int(sessions)
    if program_type == "normal":
        targets = list(current_targets or [])[:session_count]
        while len(targets) < session_count:
            targets.append({"name": f"روز {len(targets) + 1}", "targets": [], "patterns": []})
        return targets, sorted(
            {
                str(entry.get("movement_type"))
                for session in targets
                for entry in session.get("targets", [])
                if entry.get("movement_type")
            }
        )

    if program_type == "split" and session_count not in {3, 4, 5, 6}:
        raise ValidationError(_("Split فقط برای ۳ تا ۶ جلسه در هفته قالب آماده دارد."))
    if program_type == "push_pull_legs" and session_count not in {3, 6}:
        raise ValidationError(_("Push/Pull/Leg برای ۳ یا ۶ جلسه در هفته قابل استفاده است."))

    split_presets = {
        3: [
            ("سینه، سرشانه و پشت بازو", {"chest": 3, "shoulders": 3, "triceps": 2}),
            ("پا، باسن، ساق، شکم و کمر", {"legs": 3, "glutes": 1, "calves": 1, "abs": 1, "lumbar": 1}),
            ("پشت و جلو بازو", {"upper_back": 3, "lower_back": 3, "biceps": 2}),
        ],
        4: [
            ("سینه و پشت بازو", {"chest": 4, "triceps": 3, "abs": 1}),
            ("پشت و جلو بازو", {"lower_back": 4, "biceps": 3, "lumbar": 1}),
            ("پا", {"legs": 4, "glutes": 1, "calves": 1, "abs": 1}),
            ("سرشانه و پشت", {"shoulders": 4, "upper_back": 3, "lumbar": 1}),
        ],
        5: [
            ("سینه و پشت بازو", {"chest": 5, "triceps": 2}),
            ("پشت و جلو بازو", {"lower_back": 5, "biceps": 2}),
            ("پا", {"legs": 5, "glutes": 1, "calves": 2}),
            ("سرشانه، پشت بازو و شکم", {"shoulders": 5, "triceps": 1, "abs": 2}),
            ("پشت، جلو بازو و کمر", {"upper_back": 5, "biceps": 1, "lumbar": 2}),
        ],
        6: [
            ("سینه و شکم", {"chest": 6, "abs": 2}),
            ("پشت و کمر", {"lower_back": 6, "lumbar": 2}),
            ("پا", {"legs": 5, "glutes": 2, "calves": 2}),
            ("سرشانه و شکم", {"shoulders": 6, "abs": 2}),
            ("پشت و کمر", {"upper_back": 6, "lumbar": 2}),
            ("بازو", {"biceps": 4, "triceps": 4}),
        ],
    }

    if program_type == "split":
        days = [
            _section_target_day(name, counts)
            for name, counts in split_presets[session_count]
        ]
    elif program_type == "full_body":
        sections = ExerciseSecondaryMovementType.objects.filter(
            exercises__isnull=False
        ).distinct().order_by("pk")
        ordered_sections = []
        used_section_ids = set()
        for section_key in FULL_BODY_SECTION_ORDER:
            aliases = tuple(_normalized_label(value) for value in SECTION_ALIASES[section_key])
            section = next(
                (
                    candidate
                    for candidate in sections
                    if candidate.pk not in used_section_ids
                    and any(
                        alias and any(alias == label or (len(alias) > 4 and alias in label) for label in (
                            _normalized_label(candidate.name),
                            _normalized_label(candidate.name_en),
                        ))
                        for alias in aliases
                    )
                ),
                None,
            )
            if section is not None:
                ordered_sections.append(section)
                used_section_ids.add(section.pk)
        days = [
            {
                "name": f"تمام بدن — روز {index + 1}",
                "targets": [
                    {"movement_type": str(section.pk), "count": 1}
                    for section in ordered_sections
                ],
                "patterns": [],
            }
            for index in range(session_count)
        ]
        if not days or not days[0]["targets"]:
            raise ValidationError(_("برای Full Body بخش‌های هدف دارای حرکت پیدا نشدند."))
    elif program_type == "push_pull":
        days = []
        for index in range(session_count):
            push = index % 2 == 0
            pattern_key = "push" if push else "pull"
            days.append(
                _pattern_target_day(
                    "Push" if push else "Pull",
                    {pattern_key: 6, "legs": 2, "core": 1},
                )
            )
    elif program_type == "push_pull_legs":
        base_days = [
            _pattern_target_day(
                "Push",
                {"push": 6},
                section_counts={"abs": 2},
            ),
            _pattern_target_day(
                "Pull",
                {"pull": 6},
                section_counts={"abs": 2},
            ),
            _section_target_day(
                "پا و کمر",
                {"legs": 4, "glutes": 1, "calves": 2, "lumbar": 2},
            ),
        ]
        if session_count == 6:
            days = [
                {
                    **day,
                    "name": f"{day['name']} — چرخه {cycle}",
                    "targets": [dict(target) for target in day["targets"]],
                    "patterns": [dict(pattern) for pattern in day["patterns"]],
                }
                for cycle in (1, 2)
                for day in base_days
            ]
        else:
            days = base_days
    else:
        raise ValidationError(_("نوع برنامهٔ انتخاب‌شده پشتیبانی نمی‌شود."))

    selected_ids = sorted(
        {
            entry["movement_type"]
            for day in days
            for entry in day.get("targets", [])
        },
        key=int,
    )
    return days, selected_ids


def _section_target_day(name: str, counts: Mapping[str, int]) -> dict[str, Any]:
    targets = []
    for key, count in counts.items():
        section = _lookup_section(key)
        targets.append({"movement_type": str(section.pk), "count": count})
    return {"name": name, "targets": targets, "patterns": []}


def _pattern_target_day(
    name: str,
    pattern_counts: Mapping[str, int],
    *,
    section_counts: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    targets = []
    for key, count in (section_counts or {}).items():
        section = _lookup_section(key)
        targets.append({"movement_type": str(section.pk), "count": count})
    patterns = []
    for key, count in pattern_counts.items():
        pattern = _lookup_pattern(key)
        patterns.append(
            {
                "movement_pattern": str(pattern.pk),
                "count": count,
                "label": pattern.name,
            }
        )
    return {"name": name, "targets": targets, "patterns": patterns}


def _lookup_section(key: str) -> ExerciseSecondaryMovementType:
    aliases = tuple(_normalized_label(value) for value in SECTION_ALIASES[key])
    candidates = ExerciseSecondaryMovementType.objects.filter(
        exercises__isnull=False
    ).distinct().order_by("pk")
    for section in candidates:
        labels = (
            _normalized_label(section.name),
            _normalized_label(section.name_en),
        )
        if any(
            alias and any(alias == label or (len(alias) > 4 and alias in label) for label in labels)
            for alias in aliases
        ):
            return section
    raise ValidationError(
        _("بخش «%(section)s» یا حرکت‌های ثبت‌شدهٔ آن در کتابخانه پیدا نشد.")
        % {"section": key}
    )


def _lookup_pattern(key: str) -> ExerciseMovementType:
    aliases = tuple(_normalized_label(value) for value in PATTERN_ALIASES[key])
    for pattern in ExerciseMovementType.objects.order_by("pk"):
        labels = (
            _normalized_label(pattern.name),
            _normalized_label(pattern.name_en),
        )
        if any(
            alias and any(alias == label or (len(alias) > 4 and alias in label) for label in labels)
            for alias in aliases
        ):
            return pattern
    raise ValidationError(
        _("الگوی حرکت «%(pattern)s» در کتابخانه پیدا نشد.") % {"pattern": key}
    )


def program_goal_for_training_goal(training_goal: str | None) -> str | None:
    """Map the athlete-facing goal to the program prescription catalog key."""
    if not training_goal:
        return None
    return canonical_goal_key(training_goal)


def _library_fingerprint(exercises: Sequence[Exercise]) -> str:
    rows = []
    for exercise in exercises:
        rows.append(
            (
                exercise.pk,
                exercise.name,
                exercise.primary_muscle_id,
                exercise.body_part_id,
                exercise.joint_type_id,
                exercise.power_type_id,
                exercise.difficulty_level_id,
                exercise.movement_type_id,
                exercise.secondary_movement_type_id,
                exercise.equipment_type_id,
                str(exercise.equipment_type.load_increment_kg) if exercise.equipment_type_id else None,
                tuple(sorted(item.pk for item in exercise.required_equipment_types.all())),
                tuple(sorted(item.pk for item in exercise.excluded_abnormalities.all())),
            )
        )
    payload = json.dumps(rows, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _selection_reason(
    *,
    exercise: Exercise,
    target_label: str,
    exact_difficulty: bool,
    equipment_known: bool,
    profile: AthleteTrainingProfile | None,
) -> str:
    reasons = [_('برای هدف «%(target)s» در این جلسه انتخاب شد.') % {"target": target_label}]
    reasons.append(
        _("سطح حرکت با سطح انتخاب‌شده برابر است.")
        if exact_difficulty
        else _("حرکت از سطح ساده‌تر کتابخانه استفاده می‌کند.")
    )
    if equipment_known:
        reasons.append(_("تجهیزات لازم در فهرست انتخاب‌شده برای این برنامه قرار دارد."))
    else:
        reasons.append(_("تجهیزات ورزشکار ثبت نشده و باید پیش از اجرا بررسی شود."))
    if profile and profile.experience_years is not None:
        reasons.append(_("سابقهٔ ثبت‌شده: %(years)s سال.") % {"years": profile.experience_years})
    return " ".join(str(reason) for reason in reasons)


def _is_cardio_goal(goal: str) -> bool:
    normalized = " ".join(str(goal or "").casefold().replace("_", " ").split())
    return normalized in {alias.casefold() for alias in GOAL_ALIASES["cardio"]}


def _pick_varied_exercise(
    *,
    preferred_pool: Sequence[Exercise],
    pool: Sequence[Exercise],
    exposure_counts: Mapping[int, int],
    session_used_ids: set[int],
    cursor: int,
    seed_offset: int,
    fatigue_scores: Mapping[int, float] | None = None,
    favorite_ids: set[int] | None = None,
    avoided_ids: set[int] | None = None,
) -> Exercise | None:
    """Prefer recovered, favored, and less-used exercises without duplicates."""
    fatigue_scores = fatigue_scores or {}
    favorite_ids = favorite_ids or set()
    avoided_ids = avoided_ids or set()

    def rank(items):
        available = [item for item in items if item.pk not in session_used_ids]
        if not available:
            return None
        def score(item):
            return (
                fatigue_scores.get(item.primary_muscle_id, 0.0),
                1 if item.pk in avoided_ids else 0,
                0 if item.pk in favorite_ids else 1,
                exposure_counts.get(item.pk, 0),
            )

        best = min(score(item) for item in available)
        preferred = [item for item in available if score(item) == best]
        return preferred[(cursor + seed_offset) % len(preferred)]

    return rank(preferred_pool) or rank(pool)


def _order_session_items(
    items: Sequence[dict[str, Any]],
    exercise_by_id: Mapping[int, Exercise],
) -> list[dict[str, Any]]:
    """Preserve target-group order and order each group by exercise type."""
    body_part_order = {}
    indexed_items = []
    has_target_order = any("_target_order" in item for item in items)
    for item_index, item in enumerate(items):
        exercise = exercise_by_id[item["exercise"]]
        body_part_order.setdefault(exercise.body_part_id, item_index)
        indexed_items.append((item_index, item, exercise))

    indexed_items.sort(
        key=lambda entry: (
            entry[1].get("_target_order", entry[0]) if has_target_order else 0,
            0 if _is_power_exercise(entry[2]) else 1,
            body_part_order[entry[2].body_part_id],
            0 if _is_compound_exercise(entry[2]) else 1,
            entry[0],
        )
    )
    ordered_items = [item for _item_index, item, _exercise in indexed_items]
    for item in ordered_items:
        item.pop("_target_order", None)
    return ordered_items


def _is_power_exercise(exercise: Exercise) -> bool:
    """Return whether an exercise has the catalogued explosive-power type."""
    return _label_contains(exercise.power_type, POWER_TYPE_ALIASES)


def _is_compound_exercise(exercise: Exercise) -> bool:
    """Return whether an exercise targets multiple joints."""
    return _label_contains(exercise.joint_type, COMPOUND_JOINT_ALIASES)


def _label_contains(value: Any, aliases: Sequence[str]) -> bool:
    text = _normalized_label(value)
    return any(_normalized_label(alias) in text for alias in aliases)


def _normalized_label(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "name") or hasattr(value, "name_en"):
        parts = (getattr(value, "name", ""), getattr(value, "name_en", ""))
        value = " ".join(str(part) for part in parts if part)
    return (
        str(value)
        .casefold()
        .replace("ي", "ی")
        .replace("ى", "ی")
        .replace("ك", "ک")
        .replace("\u200c", "")
        .replace("-", "")
        .replace(" ", "")
    )


def _allowed_difficulty_level_ids(difficulty: ExerciseDifficultyLevel | int) -> list[int]:
    """Return the selected level and all easier levels.

    The lookup table is user-editable and historically contains both Persian
    and English labels, so the rule is intentionally based on normalized
    labels rather than hard-coded database primary keys.
    """

    difficulty_id = getattr(difficulty, "pk", difficulty)
    selected_level = (
        difficulty
        if isinstance(difficulty, ExerciseDifficultyLevel)
        else ExerciseDifficultyLevel.objects.filter(pk=difficulty_id).first()
    )
    selected_rank = _difficulty_rank(selected_level)
    if selected_rank is None:
        return [difficulty_id]
    if selected_rank == 2:
        return list(ExerciseDifficultyLevel.objects.values_list("pk", flat=True))

    return [
        level.pk
        for level in ExerciseDifficultyLevel.objects.all()
        if (level_rank := _difficulty_rank(level)) is not None
        and level_rank <= selected_rank
    ] or [difficulty_id]


def _difficulty_rank(difficulty_level: ExerciseDifficultyLevel | None) -> int | None:
    """Map a catalog difficulty label to the normalized 0-2 difficulty rank."""
    if not difficulty_level:
        return None
    text = " ".join(
        value
        for value in (
            getattr(difficulty_level, "name", ""),
            getattr(difficulty_level, "name_en", ""),
        )
        if value
    ).casefold()
    text = text.replace("ي", "ی").replace("ى", "ی").replace("ك", "ک")
    for rank in (2, 1, 0):
        if any(alias.casefold() in text for alias in DIFFICULTY_ALIASES[rank]):
            return rank
    return None


def _secondary_movement_types(
    ids: Sequence[int],
) -> QuerySet[ExerciseSecondaryMovementType]:
    """Load the selected target sections from the exercise library."""
    return ExerciseSecondaryMovementType.objects.filter(pk__in=ids)


def _selected_movement_type_ids(
    secondary_movement_counts: Mapping[Any, Any] | Sequence[Any] | None,
    session_movement_targets: Sequence[dict[str, Any]] | None,
) -> list[int]:
    """Normalize legacy and row-based target-section inputs to primary keys."""
    selected_ids = []

    def add(value):
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return
        if parsed > 0 and parsed not in selected_ids:
            selected_ids.append(parsed)

    if isinstance(secondary_movement_counts, dict):
        for movement_type_id in secondary_movement_counts:
            add(movement_type_id)
    elif isinstance(secondary_movement_counts, (list, tuple)):
        for movement_type_id in secondary_movement_counts:
            add(movement_type_id)

    if isinstance(session_movement_targets, list):
        for session_target in session_movement_targets:
            if not isinstance(session_target, dict):
                continue
            target_entries = _session_target_entries(session_target)
            if target_entries is not None:
                for movement_type_id, _count in target_entries:
                    add(movement_type_id)
                continue
            movement_types = session_target.get("movement_types")
            if movement_types is None:
                movement_types = (session_target.get("counts") or {}).keys()
            for movement_type_id in movement_types or []:
                add(movement_type_id)

    return selected_ids


def _selected_pattern_ids(
    session_movement_targets: Sequence[dict[str, Any]] | None,
) -> list[int]:
    selected_ids = []
    for session in session_movement_targets or []:
        if not isinstance(session, dict):
            continue
        for entry in session.get("patterns", []):
            if not isinstance(entry, dict):
                continue
            try:
                pattern_id = int(entry.get("movement_pattern", entry.get("id", 0)))
            except (TypeError, ValueError):
                continue
            if pattern_id > 0 and pattern_id not in selected_ids:
                selected_ids.append(pattern_id)
    return selected_ids


def _session_plans(
    *,
    sessions: int,
    selected_ids: Sequence[int],
    secondary_movement_counts: Mapping[Any, Any] | Sequence[Any] | None,
    session_movement_targets: Sequence[dict[str, Any]] | None,
    movement_type_map: Mapping[int, Any],
    pattern_type_map: Mapping[int, Any],
) -> list[dict[str, Any]]:
    """Build an ordered movement/count plan for each training session."""
    session_count = max(1, int(sessions))
    fallback_counts = (
        secondary_movement_counts
        if isinstance(secondary_movement_counts, dict)
        else {}
    )
    plans = []
    for index in range(session_count):
        target = (
            session_movement_targets[index]
            if isinstance(session_movement_targets, list)
            and index < len(session_movement_targets)
            and isinstance(session_movement_targets[index], dict)
            else None
        )
        if target is None:
            movement_types = list(selected_ids)
            counts = fallback_counts
        else:
            target_entries = _session_target_entries(target)
            if target_entries is not None:
                movement_types = [movement_type_id for movement_type_id, _count in target_entries]
                counts = {
                    str(movement_type_id): count
                    for movement_type_id, count in target_entries
                }
            else:
                movement_types = target.get("movement_types")
                counts = target.get("counts") or {}
                if movement_types is None:
                    movement_types = list(counts.keys())
        section_plan = []
        for movement_type_id in _ordered_movement_type_ids(movement_types, movement_type_map):
            count = counts.get(
                str(movement_type_id),
                counts.get(movement_type_id, 1),
            ) if isinstance(counts, dict) else 1
            try:
                count = max(0, min(20, int(count or 0)))
            except (TypeError, ValueError):
                count = 1
            if count:
                section_plan.append((movement_type_id, count))

        pattern_plan = []
        if isinstance(target, dict):
            for pattern_id, count in _pattern_target_entries(target):
                try:
                    pattern_id = int(pattern_id)
                    count = max(0, min(20, int(count or 0)))
                except (TypeError, ValueError):
                    continue
                if pattern_id in pattern_type_map and count:
                    pattern_plan.append((pattern_id, count))

        plans.append(
            {
                "name": str(target.get("name") or "") if isinstance(target, dict) else "",
                "sections": section_plan,
                "patterns": pattern_plan,
            }
        )
    return plans


def _session_target_entries(
    session_target: dict[str, Any],
) -> list[tuple[Any, Any]] | None:
    """Return row-based session targets as ``(movement_type_id, count)`` pairs.

    The editor stores each session as separate target rows. The older
    ``movement_types``/``counts`` shape remains supported for saved payloads
    and callers that use the service directly.
    """

    if not isinstance(session_target, dict) or "targets" not in session_target:
        return None
    entries = session_target.get("targets")
    if not isinstance(entries, list):
        return []
    normalized: list[tuple[Any, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        movement_type_id = entry.get(
            "movement_type",
            entry.get("movement_type_id", entry.get("target_section")),
        )
        count = entry.get(
            "count",
            entry.get("movement_count", entry.get("movements", 0)),
        )
        normalized.append((movement_type_id, count))
    return normalized


def _pattern_target_entries(
    session_target: dict[str, Any],
) -> list[tuple[Any, Any]]:
    entries = session_target.get("patterns", [])
    if not isinstance(entries, list):
        return []
    normalized = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        pattern_id = entry.get("movement_pattern", entry.get("id"))
        normalized.append((pattern_id, entry.get("count", 0)))
    return normalized


def _ordered_movement_type_ids(
    ids: Sequence[Any] | None,
    movement_type_map: Mapping[int, Any],
) -> list[int]:
    """Preserve requested order while placing core and lower-back work last."""
    ordered = []
    for value in ids or []:
        try:
            movement_type_id = int(value)
        except (TypeError, ValueError):
            continue
        if movement_type_id in movement_type_map and movement_type_id not in ordered:
            ordered.append(movement_type_id)
    return sorted(
        ordered,
        key=lambda movement_type_id: (
            _is_core_or_lower_back_movement_type(movement_type_map[movement_type_id]),
            ordered.index(movement_type_id),
        ),
    )


def _is_abdominal_movement_type(movement_type: Any) -> bool:
    """Return whether a movement section represents abdominal work."""
    text = " ".join(
        value
        for value in (
            getattr(movement_type, "name", ""),
            getattr(movement_type, "name_en", ""),
        )
        if value
    ).casefold()
    return any(
        token in text
        for token in ("شکم", "abdomen", "abdominal", "abs")
    )


def _is_core_or_lower_back_movement_type(movement_type: Any) -> bool:
    """Return whether a section is abdominal or targets the lower back."""
    if _is_abdominal_movement_type(movement_type):
        return True
    text = _normalized_label(" ".join(
        value
        for value in (
            getattr(movement_type, "name", ""),
            getattr(movement_type, "name_en", ""),
        )
        if value
    ))
    return any(
        _normalized_label(token) in text
        for token in (
            "lower back",
            "low back",
            "lumbar",
            "core",
            "central",
            "بخش پایینی پشت",
            "کمر",
            "مرکزی بدن",
            "فیله",
            "راست کننده ستون فقرات",
            "erector spinae",
        )
    )
