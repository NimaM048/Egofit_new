from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal, ROUND_DOWN
import hashlib
import json
import math
import random
import re
from typing import Any, Iterable, Mapping, Sequence

from django.core.exceptions import ValidationError
from django.db.models import Q, QuerySet
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from account.domain.exercise_goals import (
    GOAL_LOOKUP_ALIASES,
    canonical_goal_key,
    goal_lookup_aliases,
    training_goal_for_program_goal,
)
from account.domain.workout_prescriptions import (
    is_valid_repetition_prescription,
    parse_set_prescription,
)
from account.models import (
    AthleteExercisePreference,
    CardioExercise,
    CorrectiveExercise,
    Exercise,
    ExerciseDifficultyLevel,
    ExerciseStrengthEstimate,
    ExerciseMovementType,
    ExerciseSecondaryMovementType,
    ExerciseSetType,
    ExerciseRepetitionType,
    ExerciseRestType,
    ExerciseTrainingMethod,
    AthleteTrainingProfile,
    DailyMoodEntry,
    TrainingAdjustmentRecommendation,
    MuscleVolumeLandmark,
    Muscle,
    ProgramTemplate,
    WorkoutSession,
    WorkoutSetResult,
    WorkoutProgramDay,
    TrainingGoal,
)
from account.utils import normalize_digits
from account.services.training_adherence_service import TrainingAdherenceService


class AutomaticProgramService:
    """Build a conservative, editable workout payload from the exercise library.

    The generator only selects catalogued values and never invents exercise
    records. Its defaults and selection rules are part of the prescription
    contract, so changes should preserve the returned payload shape and be
    validated with ``account.tests_gym_programs``.
    """

    ALGORITHM_VERSION = "2026.11.19.1"
    SET_MATCHED_REPETITION_METHODS = frozenset({
        ExerciseTrainingMethod.PYRAMID,
        ExerciseTrainingMethod.REVERSE_PYRAMID,
        ExerciseTrainingMethod.LOW_TO_HIGH,
        ExerciseTrainingMethod.HIGH_TO_LOW,
    })

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
    METHOD_NOTES = {
        ExerciseTrainingMethod.PYRAMID: "هر ست وزنه سنگین و سپس برعکس رفتار کنید.",
        ExerciseTrainingMethod.REVERSE_PYRAMID: "هر ست وزنه سبک به سنگین پیش برود و سپس برعکس رفتار کنید.",
        ExerciseTrainingMethod.TWENTY_ONE_REPS: "21 Reps: ۷ تکرار نیمه پایین + ۷ تکرار نیمه بالا + ۷ تکرار کامل.",
        ExerciseTrainingMethod.DROP_SET: "هر ست اول تکرار کمتر و بعد با سبک کردن وزنه تکرار بعدی بلافاصله اجرا شود.",
        ExerciseTrainingMethod.LOW_TO_HIGH: "Low To High: بار را از سبک به سنگین افزایش دهید؛ تکرارها نزولی باشند.",
        ExerciseTrainingMethod.HIGH_TO_LOW: "High To Low: بار را از سنگین به سبک کاهش دهید؛ تکرارها صعودی باشند.",
    }

    @classmethod
    def build_session_targets(
        cls,
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
                cls._section_target_day(name, counts)
                for name, counts in split_presets[session_count]
            ]
        elif program_type == "full_body":
            sections = ExerciseSecondaryMovementType.objects.filter(
                exercises__isnull=False
            ).distinct().order_by("pk")
            ordered_sections = []
            used_section_ids = set()
            for section_key in cls.FULL_BODY_SECTION_ORDER:
                aliases = tuple(cls._normalized_label(value) for value in cls.SECTION_ALIASES[section_key])
                section = next(
                    (
                        candidate
                        for candidate in sections
                        if candidate.pk not in used_section_ids
                        and any(
                            alias and any(alias == label or (len(alias) > 4 and alias in label) for label in (
                                cls._normalized_label(candidate.name),
                                cls._normalized_label(candidate.name_en),
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
                    cls._pattern_target_day(
                        "Push" if push else "Pull",
                        {pattern_key: 6, "legs": 2, "core": 1},
                    )
                )
        elif program_type == "push_pull_legs":
            base_days = [
                cls._pattern_target_day(
                    "Push",
                    {"push": 6},
                    section_counts={"abs": 2},
                ),
                cls._pattern_target_day(
                    "Pull",
                    {"pull": 6},
                    section_counts={"abs": 2},
                ),
                cls._section_target_day(
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

    @classmethod
    def _section_target_day(cls, name: str, counts: Mapping[str, int]) -> dict[str, Any]:
        targets = []
        for key, count in counts.items():
            section = cls._lookup_section(key)
            targets.append({"movement_type": str(section.pk), "count": count})
        return {"name": name, "targets": targets, "patterns": []}

    @classmethod
    def _pattern_target_day(
        cls,
        name: str,
        pattern_counts: Mapping[str, int],
        *,
        section_counts: Mapping[str, int] | None = None,
    ) -> dict[str, Any]:
        targets = []
        for key, count in (section_counts or {}).items():
            section = cls._lookup_section(key)
            targets.append({"movement_type": str(section.pk), "count": count})
        patterns = []
        for key, count in pattern_counts.items():
            pattern = cls._lookup_pattern(key)
            patterns.append(
                {
                    "movement_pattern": str(pattern.pk),
                    "count": count,
                    "label": pattern.name,
                }
            )
        return {"name": name, "targets": targets, "patterns": patterns}

    @classmethod
    def _lookup_section(cls, key: str) -> ExerciseSecondaryMovementType:
        aliases = tuple(cls._normalized_label(value) for value in cls.SECTION_ALIASES[key])
        candidates = ExerciseSecondaryMovementType.objects.filter(
            exercises__isnull=False
        ).distinct().order_by("pk")
        for section in candidates:
            labels = (
                cls._normalized_label(section.name),
                cls._normalized_label(section.name_en),
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

    @classmethod
    def _lookup_pattern(cls, key: str) -> ExerciseMovementType:
        aliases = tuple(cls._normalized_label(value) for value in cls.PATTERN_ALIASES[key])
        for pattern in ExerciseMovementType.objects.order_by("pk"):
            labels = (
                cls._normalized_label(pattern.name),
                cls._normalized_label(pattern.name_en),
            )
            if any(
                alias and any(alias == label or (len(alias) > 4 and alias in label) for label in labels)
                for alias in aliases
            ):
                return pattern
        raise ValidationError(
            _("الگوی حرکت «%(pattern)s» در کتابخانه پیدا نشد.") % {"pattern": key}
        )

    @staticmethod
    def _training_profile(athlete: Any) -> AthleteTrainingProfile | None:
        if isinstance(athlete, AthleteTrainingProfile):
            return athlete
        user_id = getattr(athlete, "pk", None)
        if not user_id:
            return None
        return AthleteTrainingProfile.objects.filter(user_id=user_id).prefetch_related("available_equipment").first()

    @classmethod
    def program_goal_for_training_goal(cls, training_goal: str | None) -> str | None:
        """Map the athlete-facing goal to the program prescription catalog key."""
        if not training_goal:
            return None
        return canonical_goal_key(training_goal)

    @staticmethod
    def _resolve_seed(seed: str | int | None, **context: Any) -> str:
        if seed not in (None, ""):
            value = str(seed).strip()
            if len(value) > 64:
                raise ValidationError({"seed": _("بذر تولید نباید بیش از ۶۴ نویسه باشد.")})
            return value
        stable_context = json.dumps(context, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(stable_context.encode("utf-8")).hexdigest()[:24]

    @staticmethod
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

    @staticmethod
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

    @staticmethod
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

    @staticmethod
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

    @staticmethod
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

    @staticmethod
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

    @staticmethod
    def _exercise_preferences(athlete_id: int | None) -> dict[str, set[int]]:
        if not athlete_id:
            return {"favorite": set(), "avoid": set(), "pain": set()}
        result = {"favorite": set(), "avoid": set(), "pain": set()}
        for exercise_id, status in AthleteExercisePreference.objects.filter(user_id=athlete_id).values_list("exercise_id", "status"):
            result.setdefault(status, set()).add(exercise_id)
        return result

    @classmethod
    def _muscle_fatigue_snapshot(cls, athlete_id: int | None, experience_years=None) -> dict[int, dict[str, Any]]:
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

    @staticmethod
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

    @classmethod
    def _reduce_sets(cls, value: Any, *, factor: Decimal | None = None, decrement: int = 0) -> str:
        low, high = cls._set_count(value)
        if high <= 0:
            return str(value or "")
        if factor is not None:
            low = max(1, int((Decimal(low) * factor).quantize(Decimal("1"), rounding=ROUND_DOWN)))
            high = max(low, int((Decimal(high) * factor).quantize(Decimal("1"), rounding=ROUND_DOWN)))
        else:
            low = max(1, low - decrement)
            high = max(low, high - decrement)
        return cls._format_set_range(low, high)

    @classmethod
    def _estimated_session_minutes(cls, items: Sequence[dict[str, Any]]) -> int:
        seconds = 5 * 60 + len(items) * 30  # warm-up and transitions
        for item in items:
            low_sets, high_sets = cls._set_count(item.get("sets"))
            sets = max(1, (low_sets + high_sets) / 2)
            rep_values = [int(value) for value in re.findall(r"\d+", normalize_digits(item.get("reps") or ""))]
            reps = sum(rep_values[:2]) / min(2, len(rep_values)) if rep_values else 10
            rest_values = [int(value) for value in re.findall(r"\d+", normalize_digits(item.get("rest") or ""))]
            rest = sum(rest_values[:2]) / min(2, len(rest_values)) if rest_values else 90
            seconds += sets * ((reps * 3) + rest)
        return max(1, int((seconds + 59) // 60))

    @classmethod
    def _fit_session_duration(
        cls,
        items: list[dict[str, Any]],
        *,
        maximum_minutes: int | None,
        exercises_by_id: Mapping[int, Exercise],
    ) -> tuple[list[dict[str, Any]], int, list[str]]:
        if not maximum_minutes:
            return items, cls._estimated_session_minutes(items), []
        warnings = []
        estimate = cls._estimated_session_minutes(items)
        while estimate > maximum_minutes:
            changed = False
            for item in reversed(items):
                low, high = cls._set_count(item.get("sets"))
                if low > 2:
                    base_sets = item.get("sets")
                    item["sets"] = cls._reduce_sets(base_sets, decrement=1)
                    item["superset_sets"] = cls._reduce_sets(item.get("superset_sets") or base_sets, decrement=1)
                    item["third_sets"] = cls._reduce_sets(item.get("third_sets") or base_sets, decrement=1)
                    item["selection_reason"] = " ".join(filter(None, (
                        item.get("selection_reason", ""),
                        str(_("تعداد ست برای نزدیک‌شدن به زمان در دسترس کاهش یافت.")),
                    )))
                    changed = True
            if changed:
                estimate = cls._estimated_session_minutes(items)
                continue
            removable = next(
                (
                    item for item in reversed(items)
                    if len(items) > 2
                    and (
                        exercises_by_id.get(int(item.get("exercise") or 0)) is None
                        or not cls._is_compound_exercise(exercises_by_id[int(item["exercise"])])
                    )
                ),
                None,
            )
            if removable is None:
                break
            items.remove(removable)
            estimate = cls._estimated_session_minutes(items)
            warnings.append(str(_("یک حرکت کمکی کم‌اولویت برای رعایت مدت جلسه حذف شد؛ پیش از انتشار ترکیب روز را بررسی کنید.")))
        if estimate > maximum_minutes:
            warnings.append(
                str(_("مدت برآوردی جلسه %(estimate)s دقیقه است و از زمان ثبت‌شده (%(limit)s دقیقه) بیشتر می‌شود؛ مربی آن را بازبینی کند."))
                % {"estimate": estimate, "limit": maximum_minutes}
            )
        return items, estimate, warnings

    @classmethod
    def _weekly_muscle_volume(
        cls,
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
                low, high = cls._set_count(set_value)
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

    @classmethod
    def _weekly_volume_target(
        cls,
        goal: str,
        profile: AthleteTrainingProfile | None,
        muscle: Muscle | None = None,
        training_goal: str | None = None,
    ) -> dict[str, Any]:
        if cls._label_contains(goal, cls.GOAL_ALIASES["volume"]):
            goal_band = "hypertrophy"
            ranges = {
                "beginner": (6, 14),
                "intermediate": (8, 18),
                "experienced": (10, 20),
                "unspecified": (8, 18),
            }
        elif cls._label_contains(goal, cls.GOAL_ALIASES["strength"]) or cls._label_contains(
            goal, cls.GOAL_ALIASES["power"]
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

    @classmethod
    def _apply_weekly_volume_progression(
        cls,
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
                base_low, base_high = cls._set_count(item.get("sets"))
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
                    item_targets[str(week)] = cls._format_set_range(*counts)
                item["weekly_set_targets"] = item_targets

    def generate(
        self,
        *,
        difficulty: ExerciseDifficultyLevel | int,
        gender: str | None,
        abnormalities: Iterable[Any],
        sessions: int,
        movements: int,
        goal: str,
        training_methods: Sequence[str] | None = None,
        secondary_movement_counts: Mapping[Any, Any] | Sequence[Any] | None = None,
        session_movement_targets: Sequence[dict[str, Any]] | None = None,
        athlete: Any = None,
        training_goal: str | None = None,
        training_goal_source: str | None = None,
        seed: str | int | None = None,
        template: ProgramTemplate | None = None,
        equipment_ids: Iterable[int] | None = None,
    ) -> dict[str, Any]:
        """Generate editable daily exercise and corrective-item payloads.

        ``difficulty`` accepts either the model instance used by admin forms or
        its primary key. ``secondary_movement_counts`` and
        ``session_movement_targets`` retain both legacy and row-based input
        shapes for compatibility with saved editor payloads.
        """
        profile = self._training_profile(athlete)
        template_payload = template.payload if template and isinstance(template.payload, dict) else {}
        goal = canonical_goal_key(goal) or "general"
        if template:
            sessions = template.days_per_week
            difficulty = template.difficulty
            if not training_goal:
                training_goal = template.training_goal
                training_goal_source = "template"
            if not session_movement_targets:
                session_movement_targets = template_payload.get("session_movement_targets") or None
            if not secondary_movement_counts:
                secondary_movement_counts = template_payload.get("secondary_movement_counts") or None
        selected_training_goal = training_goal
        mapped_training_goal = self.program_goal_for_training_goal(selected_training_goal)
        if mapped_training_goal and not self._is_cardio_goal(goal):
            goal = mapped_training_goal
        athlete_id = profile.user_id if profile else getattr(athlete, "pk", None)
        profile_equipment_ids = set(
            profile.available_equipment.values_list("pk", flat=True)
        ) if profile else set()
        selected_equipment_ids = (
            profile_equipment_ids
            if equipment_ids is None
            else {int(item) for item in equipment_ids}
        )
        filter_by_equipment = equipment_ids is not None or bool(profile_equipment_ids)
        abnormalities = list(abnormalities or [])
        readiness = self._readiness_snapshot(athlete_id)
        adherence = TrainingAdherenceService.snapshot(athlete) if athlete_id else {"available": False, "percent": None, "low": False}
        requested_sessions = sessions
        sessions = TrainingAdherenceService.effective_sessions(sessions, adherence)
        approved_loads = self._approved_progression_loads(athlete_id)
        approved_deloads = self._approved_deload_recommendations(athlete_id)
        approved_rep_progressions = self._approved_rep_progressions(athlete_id)
        preferences = self._exercise_preferences(athlete_id)
        fatigue = self._muscle_fatigue_snapshot(
            athlete_id,
            profile.experience_years if profile else None,
        )
        fatigue_scores = {muscle_id: value["fatigue"] for muscle_id, value in fatigue.items()}
        last_performance = self._last_performance_snapshot(athlete_id)
        seed_value = self._resolve_seed(
            seed,
            algorithm_version=self.ALGORITHM_VERSION,
            athlete_id=athlete_id,
            goal=goal,
            sessions=sessions,
            movements=movements,
            difficulty=getattr(difficulty, "pk", difficulty),
            targets=session_movement_targets,
            equipment=sorted(selected_equipment_ids),
            session_duration_minutes=profile.session_duration_minutes if profile else None,
            readiness=readiness,
            adherence=adherence,
            muscle_fatigue=fatigue,
            exercise_preferences={key: sorted(value) for key, value in preferences.items()},
            approved_loads=sorted((exercise_id, str(item.reviewed_load_kg)) for exercise_id, item in approved_loads.items()),
            approved_deloads=sorted((exercise_id, item.pk) for exercise_id, item in approved_deloads.items()),
            approved_rep_progressions=sorted((exercise_id, item.pk) for exercise_id, item in approved_rep_progressions.items()),
            template_id=template.pk if template else None,
            template_updated_at=template.updated_at.isoformat() if template else None,
        )
        rng = random.Random(seed_value)
        seed_offset = int(hashlib.sha256(seed_value.encode("utf-8")).hexdigest()[:8], 16)
        if self._is_cardio_goal(goal):
            return self._generate_cardio(
                sessions=sessions,
                goal="cardio",
                profile=profile,
                equipment_ids=selected_equipment_ids,
                filter_by_equipment=filter_by_equipment,
                rng=rng,
                seed_value=seed_value,
                readiness=readiness,
                adherence={**adherence, "requested_sessions": requested_sessions, "effective_sessions": sessions},
                training_goal=selected_training_goal,
                training_goal_source=training_goal_source,
            )

        selected_ids = self._selected_movement_type_ids(
            secondary_movement_counts,
            session_movement_targets,
        )
        selected_pattern_ids = self._selected_pattern_ids(session_movement_targets)
        movement_type_map = {
            item.pk: item for item in self._secondary_movement_types(selected_ids)
        }
        pattern_type_map = {
            item.pk: item
            for item in ExerciseMovementType.objects.filter(pk__in=selected_pattern_ids)
        }
        selected_ids = [pk for pk in selected_ids if pk in movement_type_map]
        selected_pattern_ids = [pk for pk in selected_pattern_ids if pk in pattern_type_map]
        if not selected_ids and not selected_pattern_ids:
            raise ValidationError(_("برای جلسه‌ها بخش عضله یا الگوی حرکت انتخاب نشده است."))

        prescription_catalogs = self._prescription_catalogs(
            goal,
            ExerciseTrainingMethod.NORMAL,
        )
        requested_methods = list(
            dict.fromkeys(
                method
                for method in (training_methods or [])
                if method in dict(ExerciseTrainingMethod.choices)
                and method != ExerciseTrainingMethod.NORMAL
            )
        )
        method_catalogs = {}
        for method in requested_methods:
            method_catalogs[method] = self._prescription_catalogs(goal, method)
        difficulty_id = getattr(difficulty, "pk", difficulty)
        allowed_difficulty_ids = self._allowed_difficulty_level_ids(difficulty)
        exercise_filter = Q()
        if selected_ids:
            exercise_filter |= Q(secondary_movement_type_id__in=selected_ids)
        if selected_pattern_ids:
            exercise_filter |= Q(movement_type_id__in=selected_pattern_ids)
        queryset = Exercise.objects.select_related(
            "primary_muscle",
            "secondary_muscle",
            "equipment_type",
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

        session_plans = self._session_plans(
            sessions=sessions,
            selected_ids=selected_ids,
            secondary_movement_counts=secondary_movement_counts,
            session_movement_targets=session_movement_targets,
            movement_type_map=movement_type_map,
            pattern_type_map=pattern_type_map,
        )
        days = []
        weekly_direct_sets = defaultdict(lambda: [0, 0])
        weekly_direct_section_names = {}
        has_explicit_session_targets = bool(session_movement_targets)
        exercise_by_id = {exercise.pk: exercise for exercise in exercises}
        exposure_counts = defaultdict(int)
        rotation_cursors = defaultdict(int)
        substitution_count = 0
        applied_progression_ids = set()
        applied_deload_ids = set()
        duration_warnings = []
        has_explicit_movement_counts = isinstance(secondary_movement_counts, dict)
        for day_number, session_plan in enumerate(session_plans, start=1):
            items = []
            session_used_exercise_ids = set()
            shortages = []
            section_slots = [
                ("section", movement_type_id, count)
                for movement_type_id, count in session_plan["sections"]
            ]
            pattern_slots = [
                ("pattern", movement_type_id, count)
                for movement_type_id, count in session_plan["patterns"]
            ]
            primary_pattern_slots = [
                slot for slot in pattern_slots
                if not self._is_core_or_lower_back_movement_type(pattern_type_map[slot[1]])
            ]
            finishing_pattern_slots = [
                slot for slot in pattern_slots
                if self._is_core_or_lower_back_movement_type(pattern_type_map[slot[1]])
            ]
            primary_section_slots = [
                slot for slot in section_slots
                if not self._is_core_or_lower_back_movement_type(movement_type_map[slot[1]])
            ]
            finishing_section_slots = [
                slot for slot in section_slots
                if self._is_core_or_lower_back_movement_type(movement_type_map[slot[1]])
            ]
            # PPL patterns and primary split sections stay ahead of abdominal
            # and lower-back work, regardless of how the target rows are stored.
            slots = (
                primary_pattern_slots
                + primary_section_slots
                + finishing_section_slots
                + finishing_pattern_slots
            )
            for target_order, (target_kind, movement_type_id, count) in enumerate(slots):
                target_key = (target_kind, movement_type_id)
                preferred_pool = preferred_by_target.get(target_key, [])
                pool = by_target.get(target_key, [])
                target_label = (
                    movement_type_map[movement_type_id].name
                    if target_kind == "section" and movement_type_id in movement_type_map
                    else pattern_type_map[movement_type_id].name
                )
                added = 0
                for offset in range(count):
                    if (
                        not has_explicit_session_targets
                        and not has_explicit_movement_counts
                        and len(items) >= int(movements)
                    ):
                        shortages.append(f"{target_label}: {count - added}")
                        break
                    if not pool:
                        shortages.append(f"{target_label}: {count - added}")
                        break
                    exercise = self._pick_varied_exercise(
                        preferred_pool=preferred_pool,
                        pool=pool,
                        exposure_counts=exposure_counts,
                        session_used_ids=session_used_exercise_ids,
                        cursor=rotation_cursors[target_key],
                        seed_offset=seed_offset,
                        fatigue_scores=fatigue_scores,
                        favorite_ids=preferences["favorite"],
                        avoided_ids=preferences["avoid"],
                    )
                    used_substitute = False
                    if exercise is None:
                        substitute_pool = fallback_by_target.get(target_key, [])
                        exercise = self._pick_varied_exercise(
                            preferred_pool=[item for item in substitute_pool if item.difficulty_level_id == difficulty_id],
                            pool=substitute_pool,
                            exposure_counts=exposure_counts,
                            session_used_ids=session_used_exercise_ids,
                            cursor=rotation_cursors[target_key],
                            seed_offset=seed_offset,
                            fatigue_scores=fatigue_scores,
                            favorite_ids=preferences["favorite"],
                            avoided_ids=preferences["avoid"],
                        )
                        used_substitute = exercise is not None
                    if exercise is None:
                        shortages.append(f"{target_label}: {count - added}")
                        break
                    rotation_cursors[target_key] += 1
                    exposure_counts[exercise.pk] += 1
                    session_used_exercise_ids.add(exercise.pk)
                    prescription = self._prescription_for_index(
                        prescription_catalogs,
                        len(items),
                        method=ExerciseTrainingMethod.NORMAL,
                    )
                    reason = self._selection_reason(
                        exercise=exercise,
                        target_label=target_label,
                        exact_difficulty=exercise.difficulty_level_id == difficulty_id,
                        equipment_known=bool(selected_equipment_ids),
                        profile=profile,
                    )
                    if used_substitute:
                        substitution_count += 1
                        reason += " " + str(_("جایگزین هم‌عضله و هم‌الگوی حرکت انتخاب شد؛ حرکت اصلی این بخش با محدودیت تجهیزات یا ناهنجاری روبه‌رو بود."))
                    elif excluded_targets.get(target_key):
                        reason += " " + str(_("این حرکت از گزینه‌های مجاز همین هدف انتخاب شد؛ گزینه‌های دیگر به‌دلیل تجهیزات یا محدودیت ثبت‌شده کنار گذاشته شدند."))
                    if exercise.pk in preferences["favorite"]:
                        reason += " " + str(_("این حرکت در ترجیحات ورزشکار محبوب ثبت شده است."))
                    if exercise.pk in preferences["avoid"]:
                        reason += " " + str(_("حرکت در فهرست اجتناب ثبت شده بود؛ به‌دلیل کمبود جایگزین هم‌هدف نگه داشته شد و بازبینی مربی لازم است."))
                    progression = approved_loads.get(exercise.pk)
                    deload = approved_deloads.get(exercise.pk)
                    if progression and not deload:
                        reason += " " + str(_("بار %(load)s کیلوگرم از پیشنهاد پیشرفتِ تأییدشده توسط مربی در تاریخ %(date)s استفاده شد.") % {
                            "load": progression.reviewed_load_kg,
                            "date": timezone.localtime(progression.reviewed_at).strftime("%Y-%m-%d") if progression.reviewed_at else "—",
                        })
                    items.append(
                        {
                            "_target_order": target_order,
                            "exercise": exercise.pk,
                            "generated_exercise": exercise.pk,
                            "exercise_name": exercise.name,
                            "goal": goal,
                            "superset_exercise": "",
                            "third_exercise": "",
                            "sets": prescription["sets"],
                            "reps": prescription["reps"],
                            "rest": prescription["rest"],
                            "superset_sets": prescription["sets"],
                            "superset_reps": prescription["reps"],
                            "superset_rest": prescription["rest"],
                            "third_sets": prescription["sets"],
                            "third_reps": prescription["reps"],
                            "third_rest": prescription["rest"],
                            "note": "",
                            "training_method": ExerciseTrainingMethod.NORMAL,
                            "selection_reason": reason,
                            "selection_score": min(
                                100 if not used_substitute else 74,
                                75
                                + (15 if exercise.difficulty_level_id == difficulty_id else 5)
                                + (10 if selected_equipment_ids else 0),
                            ),
                            "target_load_kg": str(progression.reviewed_load_kg) if progression else "",
                            "target_rpe": 8 if progression else "",
                            "target_rir": 2 if progression else "",
                            "warmup_sets": [],
                            "previous_performance": last_performance.get(exercise.pk, {}),
                            "weekly_set_targets": {},
                        }
                    )
                    if progression:
                        applied_progression_ids.add(progression.pk)
                    if deload:
                        applied_progression_ids.add(deload.pk)

            available_methods = [method for method in requested_methods if method_catalogs.get(method)]
            if available_methods and items:
                special_count = rng.randint(1, min(2, len(items)))
                for item_index in rng.sample(range(len(items)), special_count):
                    method = rng.choice(available_methods)
                    prescription = self._prescription_for_index(
                        method_catalogs[method],
                        item_index,
                        method=method,
                    )
                    items[item_index].update(
                        {
                            "sets": prescription["sets"],
                            "reps": prescription["reps"],
                            "rest": prescription["rest"],
                            "superset_sets": prescription["sets"],
                            "superset_reps": prescription["reps"],
                            "superset_rest": prescription["rest"],
                            "third_sets": prescription["sets"],
                            "third_reps": prescription["reps"],
                            "third_rest": prescription["rest"],
                            "note": self.METHOD_NOTES[method],
                            "training_method": method,
                        }
                    )
            items = self._order_session_items(items, exercise_by_id)
            for item in items:
                exercise_id = int(item.get("exercise") or 0)
                recommendation = approved_rep_progressions.get(exercise_id)
                if recommendation is None or exercise_id in approved_loads:
                    continue
                evidence = recommendation.evidence if isinstance(recommendation.evidence, dict) else {}
                target_reps = evidence.get("next_rep_target")
                rep_values = [
                    int(value)
                    for value in re.findall(r"\d+", normalize_digits(item.get("reps") or ""))
                ]
                if (
                    not isinstance(target_reps, int)
                    or len(rep_values) < 2
                    or not rep_values[0] < target_reps <= 15
                ):
                    continue
                item["reps_min"] = rep_values[0]
                item["reps_max"] = target_reps
                previous = item.get("previous_performance") or {}
                base_load = recommendation.reviewed_load_kg or recommendation.baseline_load_kg or previous.get("load_kg")
                if base_load:
                    item["target_load_kg"] = str(base_load)
                item["selection_reason"] = " ".join(filter(None, (
                    item.get("selection_reason", ""),
                    str(_("پیشنهاد تکرار تأییدشده توسط مربی اعمال شد؛ بار فعلی حفظ و سقف تکرار به %(target)s رسید.") % {"target": target_reps}),
                )))
                item["note"] = " ".join(filter(None, (
                    item.get("note", ""),
                    str(_("بار فعلی را حفظ کنید و هر ست را تا %(target)s تکرار پیش ببرید.") % {"target": target_reps}),
                )))
                applied_progression_ids.add(recommendation.pk)
            first_compound = next(
                (item for item in items if self._is_compound_exercise(exercise_by_id[item["exercise"]])),
                None,
            )
            if first_compound:
                exercise = exercise_by_id[first_compound["exercise"]]
                previous = first_compound.get("previous_performance") or {}
                working_load = Decimal(str(first_compound.get("target_load_kg") or previous.get("load_kg") or "0"))
                increment = exercise.equipment_type.load_increment_kg if exercise.equipment_type_id else None
                increment = Decimal(str(increment or "0.5"))
                if working_load > 0:
                    warmup = []
                    for percent in (50, 70, 85):
                        load = ((working_load * Decimal(percent) / Decimal(100)) / increment).quantize(
                            Decimal("1"), rounding=ROUND_DOWN
                        ) * increment
                        if load > 0:
                            warmup.append({"percent": percent, "load_kg": str(load.quantize(Decimal("0.1"))), "reps": 5})
                    first_compound["warmup_sets"] = warmup
            if readiness.get("low"):
                for item in items:
                    for field in ("sets", "superset_sets", "third_sets"):
                        if item.get(field):
                            item[field] = self._reduce_sets(item[field], decrement=1)
                    if item.get("target_load_kg"):
                        reduced_load = Decimal(str(item["target_load_kg"])) * Decimal("0.95")
                        item["target_load_kg"] = str(reduced_load.quantize(Decimal("0.1"), rounding=ROUND_DOWN))
                    item["target_rpe"] = 7
                    item["target_rir"] = 3
                    item["selection_reason"] = " ".join(filter(None, (
                        item.get("selection_reason", ""),
                        str(_("آمادگی و خواب دو یا چند ثبت اخیر پایین بود؛ ست‌ها یک پله کم و بار هدف حداکثر ۵٪ سبک‌تر شد.")),
                    )))
            deloaded_muscle_ids = {
                int(recommendation.evidence.get("muscle_id"))
                for recommendation in approved_deloads.values()
                if isinstance(recommendation.evidence, dict) and recommendation.evidence.get("muscle_id")
            }
            deload_ids_used_today = set()
            for item in items:
                exercise = exercise_by_id[item["exercise"]]
                recommendation = approved_deloads.get(exercise.pk)
                muscle_ids = {exercise.primary_muscle_id, exercise.secondary_muscle_id} - {None}
                if muscle_ids.intersection(deloaded_muscle_ids):
                    for field in ("sets", "superset_sets", "third_sets"):
                        if item.get(field):
                            item[field] = self._reduce_sets(item[field], factor=Decimal("0.5"))
                    item["target_rpe"] = 7
                    item["target_rir"] = 3
                    item["selection_reason"] = " ".join(filter(None, (item.get("selection_reason", ""), str(_("مربی پس از افت پایدار e1RM هفته سبک را تأیید کرد؛ ست‌های این عضله ۵۰٪ کاهش یافتند.")))))
                    for approved in approved_deloads.values():
                        if isinstance(approved.evidence, dict) and approved.evidence.get("muscle_id") in muscle_ids:
                            deload_ids_used_today.add(approved.pk)
                if recommendation:
                    reviewed_load = recommendation.reviewed_load_kg or recommendation.suggested_load_kg
                    if reviewed_load:
                        item["target_load_kg"] = str(reviewed_load)
                    deload_ids_used_today.add(recommendation.pk)
                    item["selection_reason"] = " ".join(filter(None, (item.get("selection_reason", ""), str(_("بار حرکت لنگر با تأیید مربی ۱۰٪ کاهش یافت.")))))
            if any(fatigue_scores.get(exercise_by_id[item["exercise"]].primary_muscle_id, 0) >= 75 for item in items):
                for item in items:
                    exercise = exercise_by_id[item["exercise"]]
                    fatigue_value = fatigue_scores.get(exercise.primary_muscle_id, 0)
                    if fatigue_value >= 75:
                        item["sets"] = self._reduce_sets(item["sets"], decrement=1)
                        item["selection_reason"] = " ".join(filter(None, (item.get("selection_reason", ""), str(_("خستگی عضلهٔ هدف از تمرین‌های اخیر بالا بود؛ یک ست کم شد.")))))
            applied_deload_ids.update(deload_ids_used_today)
            items, estimated_minutes, day_duration_warnings = self._fit_session_duration(
                items,
                maximum_minutes=profile.session_duration_minutes if profile else None,
                exercises_by_id=exercise_by_id,
            )
            duration_warnings.extend(day_duration_warnings)
            for item in items:
                exercise = exercise_by_id[item["exercise"]]
                if exercise.secondary_movement_type_id:
                    minimum_sets, maximum_sets = self._set_count(item.get("sets"))
                    weekly_direct_sets[exercise.secondary_movement_type_id][0] += minimum_sets
                    weekly_direct_sets[exercise.secondary_movement_type_id][1] += maximum_sets
                    weekly_direct_section_names[exercise.secondary_movement_type_id] = (
                        exercise.secondary_movement_type.name
                    )
            day_notes = f"تنظیمات هدف «{goal}»؛ قابل ویرایش توسط ادمین."
            day_notes += f" برآورد مدت: {estimated_minutes} دقیقه."
            if not items:
                day_notes += " برای هدف‌های این جلسه حرکت قابل‌تولید پیدا نشد؛ مربی می‌تواند در پیش‌نمایش حرکت اضافه کند."
            if shortages:
                day_notes += " تعداد حرکت قابل‌تأمین نبود: " + "، ".join(shortages)
            if profile and profile.session_duration_minutes:
                day_notes += f" سقف زمان ثبت‌شده: {profile.session_duration_minutes} دقیقه."
            days.append(
                {
                    "name": session_plan["name"] or f"روز {day_number}",
                    "notes": day_notes,
                    "estimated_duration_minutes": estimated_minutes,
                    "items": items,
                    "_fatigue_sort": sum(
                        fatigue_scores.get(exercise_by_id[item["exercise"]].primary_muscle_id, 0)
                        for item in items
                    ) / max(1, len(items)),
                }
            )

        if len(days) > 1 and not has_explicit_session_targets:
            days.sort(key=lambda day: day.get("_fatigue_sort", 0))
            for day in days:
                day.pop("_fatigue_sort", None)
        else:
            for day in days:
                day.pop("_fatigue_sort", None)
        applied_progression_ids.update(applied_deload_ids)

        corrective_items = []
        abnormality_ids = [item.pk for item in abnormalities]
        corrective_qs = CorrectiveExercise.objects.select_related("abnormality_type").filter(
            abnormality_type_id__in=abnormality_ids
        )
        for index, corrective in enumerate(corrective_qs[: min(len(abnormality_ids) * 2, 12)], start=1):
            corrective_items.append(
                {
                    "corrective_exercise": corrective.pk,
                    "corrective_exercise_name": corrective.name,
                    "phase": "warmup" if index % 2 else "cooldown",
                    "sets": "2",
                    "reps": "10-12",
                    "note": corrective.abnormality_type.name if corrective.abnormality_type else "",
                }
            )
        volume_warnings = []
        profile_warnings = []
        if profile is None:
            profile_warnings.append(_("پروفایل تمرینی ورزشکار پیدا نشد؛ انتخاب تجهیزات و سطح تجربه با اطمینان پایین انجام شد."))
        elif not selected_equipment_ids:
            profile_warnings.append(_("تجهیزاتی انتخاب نشده است؛ سازگاری حرکت‌ها با امکانات ورزشکار قابل تأیید نیست."))
        if selected_training_goal:
            profile_goal_alias = self.program_goal_for_training_goal(selected_training_goal)
            if profile_goal_alias and not self._label_contains(goal, self.GOAL_ALIASES.get(profile_goal_alias, (profile_goal_alias,))):
                profile_warnings.append(_("هدف برنامه با هدف اصلی انتخاب‌شده فرق دارد؛ بازبینی مربی پیشنهاد می‌شود."))
        if profile:
            if profile.experience_years is not None and profile.experience_years <= 1:
                if self._difficulty_rank(ExerciseDifficultyLevel.objects.filter(pk=difficulty_id).first()) not in (None, 0):
                    profile_warnings.append(_("سابقه ثبت‌شده حداکثر یک سال است، اما سطح دشواری بالاتر انتخاب شده؛ مربی پیش از انتشار بررسی کند."))
            if profile.sessions_per_week and profile.sessions_per_week != sessions:
                profile_warnings.append(_("تعداد جلسه‌های این برنامه با تعداد ثبت‌شده در پروفایل تفاوت دارد؛ این انتخاب ادمین را بازبینی کنید."))
        if abnormality_ids:
            profile_warnings.append(_("محدودیت‌های ثبت‌شده فقط با مواردی که مربی در کتابخانه به حرکت پیوند داده فیلتر شدند؛ بازبینی مربی لازم است."))
        if excluded_by_equipment:
            profile_warnings.append(_("%(count)s حرکت به‌دلیل تجهیزات در دسترس حذف شد.") % {"count": excluded_by_equipment})
        if excluded_by_restriction:
            profile_warnings.append(_("%(count)s حرکت به‌دلیل محدودیت نشانه‌گذاری‌شده در کتابخانه حذف شد.") % {"count": excluded_by_restriction})
        weekly_muscle_volume = self._weekly_muscle_volume(days, exercise_by_id)
        muscles_by_name = {}
        for exercise in exercise_by_id.values():
            if exercise.primary_muscle_id:
                muscles_by_name[exercise.primary_muscle.name] = exercise.primary_muscle
            if exercise.secondary_muscle_id:
                muscles_by_name[exercise.secondary_muscle.name] = exercise.secondary_muscle
        weekly_volume_targets = {
            muscle_name: self._weekly_volume_target(
                mapped_training_goal or goal,
                profile,
                muscle=muscles_by_name.get(muscle_name),
                training_goal=selected_training_goal,
            )
            for muscle_name in weekly_muscle_volume
        }
        cycle_weeks = template.weeks if template else 4
        self._apply_weekly_volume_progression(
            days,
            exercise_by_id,
            weekly_muscle_volume,
            weekly_volume_targets,
            cycle_weeks=cycle_weeks,
        )
        weekly_muscle_volume_by_week = {
            str(week): self._weekly_muscle_volume(days, exercise_by_id, week=week)
            for week in range(1, cycle_weeks + 1)
        }
        for muscle_name, volumes in weekly_muscle_volume.items():
            volume_target = weekly_volume_targets[muscle_name]
            lower, upper = volumes["weighted_total_sets"]
            if upper < volume_target["minimum"] or lower > volume_target["maximum"]:
                volume_warnings.append(
                    _("حجم مؤثر هفتگی «%(muscle)s» در شروع چرخه حدود %(sets)s ست است؛ مرزهای ثبت‌شده برای این هدف MEV=%(minimum)s، MAV=%(optimum)s و MRV=%(maximum)s ست هستند. ست مستقیم کامل و ست غیرمستقیم با ضریب نیم‌ست شمرده شده‌اند.")
                    % {
                        "muscle": muscle_name,
                        "sets": self._format_set_range(int(lower), int(upper)),
                        "minimum": volume_target["minimum"],
                        "optimum": volume_target["optimum"],
                        "maximum": volume_target["maximum"],
                    }
                )
        if adherence.get("low"):
            profile_warnings.append(_("پایبندی ۳۰روزه %(percent)s٪ است؛ تعداد جلسه‌ها از %(requested)s به %(effective)s کاهش یافت تا برنامه قابل اجرا بماند.") % {
                "percent": adherence["percent"],
                "requested": requested_sessions,
                "effective": sessions,
            })
        if readiness.get("low"):
            profile_warnings.append(_("امتیاز ترکیبی آمادگی، خواب، خستگی و کوفتگی در حداقل دو ثبت هفتهٔ اخیر پایین بوده؛ حجم و شدت هدف محافظه‌کارانه تعدیل شد و بازبینی مربی لازم است."))
        elif readiness.get("samples") == 1:
            profile_warnings.append(_("برای تعدیل ریکاوری، دست‌کم دو ثبت وضعیت در هفت روز اخیر لازم است؛ یک ثبت منفرد مبنای تغییر برنامه نشد."))
        schedule = list(profile.preferred_days or []) if profile else []
        valid_days = [day for day in schedule if day in WorkoutProgramDay.Weekday.values]
        if profile and valid_days and len(valid_days) < len(days):
            profile_warnings.append(_("برای همه جلسه‌ها روز ترجیحی کافی ثبت نشده است؛ روزهای باقی‌مانده بدون زمان‌بندی نگه داشته شدند."))
        weekday_labels = dict(WorkoutProgramDay.Weekday.choices)
        for index, day in enumerate(days):
            if index < len(valid_days):
                day["scheduled_weekday"] = valid_days[index]
                day["schedule_label"] = str(weekday_labels[valid_days[index]])
        library_fingerprint = self._library_fingerprint(candidate_rows)
        metadata = {
            "algorithm_version": self.ALGORITHM_VERSION,
            "seed": seed_value,
            "athlete_id": athlete_id,
            "profile_updated_at": profile.updated_at.isoformat() if profile else None,
            "profile_goal": profile.primary_goal if profile else None,
            "program_goal": goal,
            "selected_training_goal": selected_training_goal,
            "training_goal_source": training_goal_source or ("admin" if training_goal else "program_goal"),
            "experience_years": profile.experience_years if profile else None,
            "session_duration_minutes": profile.session_duration_minutes if profile else None,
            "preferred_days": valid_days,
            "readiness": readiness,
            "adherence": {
                **adherence,
                "requested_sessions": requested_sessions,
                "effective_sessions": sessions,
            },
            "muscle_fatigue": [
                {"muscle_id": muscle_id, **values}
                for muscle_id, values in sorted(fatigue.items(), key=lambda row: row[1]["name"])
            ],
            "exercise_preference_counts": {key: len(value) for key, value in preferences.items()},
            "template": {
                "id": template.pk,
                "title": template.title,
                "updated_at": template.updated_at.isoformat(),
                "weeks": template.weeks,
            } if template else None,
            "equipment_ids": sorted(selected_equipment_ids),
            "selected_exercise_ids": [item["exercise"] for day in days for item in day["items"]],
            "eligible_exercise_count": len(exercises),
            "excluded_equipment_count": excluded_by_equipment,
            "excluded_restriction_count": excluded_by_restriction,
            "substitution_count": substitution_count,
            "progression_recommendations_applied": len(applied_progression_ids),
            "progression_recommendation_ids": sorted(applied_progression_ids),
            "weekly_muscle_volume": weekly_muscle_volume,
            "weekly_muscle_volume_by_week": weekly_muscle_volume_by_week,
            "weekly_volume_targets": weekly_volume_targets,
            "weekly_volume_target": self._weekly_volume_target(mapped_training_goal or goal, profile, training_goal=selected_training_goal),
            "periodization": {
                "cycle_weeks": cycle_weeks,
                "deload_week": cycle_weeks if cycle_weeks > 1 else None,
                "deload_sets_multiplier": 0.5,
                "volume_progression": "MEV_to_MAV",
                "description": (
                    str(_("ست‌های هر عضله از MEV به‌تدریج به MAV نزدیک می‌شوند و هفتهٔ %(week)s با نصف‌کردن حجم سبک‌تر اجرا می‌شود. پیشنهاد هفتهٔ سبک زودتر پس از افت عملکرد فقط با تأیید مربی اعمال می‌شود.") % {"week": cycle_weeks})
                    if cycle_weeks > 1
                    else str(_("چرخه یک‌هفته‌ای است؛ حجم از حداقل مؤثر هدف تعیین می‌شود و هفتهٔ سبک دوره‌ای اعمال نمی‌شود."))
                ),
            },
            "library_fingerprint": library_fingerprint,
            "human_review_required": bool(abnormality_ids) or not selected_equipment_ids or bool(readiness.get("low")) or bool(adherence.get("low")) or any(value["fatigue"] >= 75 for value in fatigue.values()) or bool(duration_warnings) or bool(substitution_count),
        }
        return {
            "days": days,
            "correctives": corrective_items,
            "warnings": volume_warnings + profile_warnings + duration_warnings,
            "metadata": metadata,
        }

    @staticmethod
    def _set_count(value: Any) -> tuple[int, int]:
        """Return a safe weekly-volume estimate for an exact or ranged set count."""
        return parse_set_prescription(value) or (0, 0)

    @staticmethod
    def _format_set_range(minimum: int, maximum: int) -> str:
        return str(minimum) if minimum == maximum else f"{minimum}–{maximum}"

    @classmethod
    def _prescription_catalogs(cls, goal: str, method: str) -> dict[str, list[str]]:
        catalogs = {
            "sets": cls._ordered_catalog(
                cls._lookup_values(ExerciseSetType, "set_count", goal, method)
            ),
            "reps": cls._ordered_catalog(
                cls._lookup_values(ExerciseRepetitionType, "reps", goal, method)
            ),
            "rest": cls._ordered_catalog(
                cls._lookup_values(ExerciseRestType, "rest_time", goal, method)
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
                if cls._valid_repetition_sequence(value, method)
            ]
        if any(not values for values in catalogs.values()):
            method_label = dict(ExerciseTrainingMethod.choices).get(method, method)
            raise ValidationError(
                _("برای هدف «%(goal)s» و روش «%(method)s» باید ست، تکرار و استراحت در کتابخانه ثبت شود.")
                % {"goal": goal, "method": method_label}
            )
        return catalogs

    @classmethod
    def _is_cardio_goal(cls, goal: str) -> bool:
        normalized = " ".join(str(goal or "").casefold().replace("_", " ").split())
        return normalized in {alias.casefold() for alias in cls.GOAL_ALIASES["cardio"]}

    @classmethod
    def _generate_cardio(
        cls,
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
                "algorithm_version": cls.ALGORITHM_VERSION,
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

    @classmethod
    def _valid_repetition_sequence(cls, value: str, method: str) -> bool:
        return is_valid_repetition_prescription(value, method)

    @staticmethod
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

    @staticmethod
    def _ordered_catalog(values: Sequence[str]) -> list[str]:
        """Keep unique catalog values in priority order for repeatable variation."""
        return list(dict.fromkeys(value for value in values if value))

    @classmethod
    def _prescription_for_index(
        cls,
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
                (set_value, cls._repetitions_compatible_with_sets(repetition_values, set_value, method))
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

    @classmethod
    def _repetitions_compatible_with_sets(
        cls,
        repetition_values: Sequence[str],
        set_value: str,
        method: str,
    ) -> list[str]:
        """Match sequence-based prescriptions to the number of selected sets."""
        if method not in cls.SET_MATCHED_REPETITION_METHODS:
            return list(repetition_values)

        minimum_sets, maximum_sets = cls._set_count(set_value)
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

    @classmethod
    def _order_session_items(
        cls,
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
                0 if cls._is_power_exercise(entry[2]) else 1,
                body_part_order[entry[2].body_part_id],
                0 if cls._is_compound_exercise(entry[2]) else 1,
                entry[0],
            )
        )
        ordered_items = [item for _item_index, item, _exercise in indexed_items]
        for item in ordered_items:
            item.pop("_target_order", None)
        return ordered_items

    @classmethod
    def _is_power_exercise(cls, exercise: Exercise) -> bool:
        """Return whether an exercise has the catalogued explosive-power type."""
        return cls._label_contains(exercise.power_type, cls.POWER_TYPE_ALIASES)

    @classmethod
    def _is_compound_exercise(cls, exercise: Exercise) -> bool:
        """Return whether an exercise targets multiple joints."""
        return cls._label_contains(exercise.joint_type, cls.COMPOUND_JOINT_ALIASES)

    @classmethod
    def _label_contains(cls, value: Any, aliases: Sequence[str]) -> bool:
        text = cls._normalized_label(value)
        return any(cls._normalized_label(alias) in text for alias in aliases)

    @staticmethod
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

    @classmethod
    def _allowed_difficulty_level_ids(cls, difficulty: ExerciseDifficultyLevel | int) -> list[int]:
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
        selected_rank = cls._difficulty_rank(selected_level)
        if selected_rank is None:
            return [difficulty_id]
        if selected_rank == 2:
            return list(ExerciseDifficultyLevel.objects.values_list("pk", flat=True))

        return [
            level.pk
            for level in ExerciseDifficultyLevel.objects.all()
            if (level_rank := cls._difficulty_rank(level)) is not None
            and level_rank <= selected_rank
        ] or [difficulty_id]

    @classmethod
    def _difficulty_rank(cls, difficulty_level: ExerciseDifficultyLevel | None) -> int | None:
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
            if any(alias.casefold() in text for alias in cls.DIFFICULTY_ALIASES[rank]):
                return rank
        return None

    @staticmethod
    def _secondary_movement_types(
        ids: Sequence[int],
    ) -> QuerySet[ExerciseSecondaryMovementType]:
        """Load the selected target sections from the exercise library."""
        return ExerciseSecondaryMovementType.objects.filter(pk__in=ids)

    @classmethod
    def _selected_movement_type_ids(
        cls,
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
                target_entries = cls._session_target_entries(session_target)
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

    @staticmethod
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

    @classmethod
    def _session_plans(
        cls,
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
                target_entries = cls._session_target_entries(target)
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
            for movement_type_id in cls._ordered_movement_type_ids(movement_types, movement_type_map):
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
                for pattern_id, count in cls._pattern_target_entries(target):
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

    @staticmethod
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

    @staticmethod
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

    @classmethod
    def _ordered_movement_type_ids(
        cls,
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
                cls._is_core_or_lower_back_movement_type(movement_type_map[movement_type_id]),
                ordered.index(movement_type_id),
            ),
        )

    @staticmethod
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

    @classmethod
    def _is_core_or_lower_back_movement_type(cls, movement_type: Any) -> bool:
        """Return whether a section is abdominal or targets the lower back."""
        if cls._is_abdominal_movement_type(movement_type):
            return True
        text = cls._normalized_label(" ".join(
            value
            for value in (
                getattr(movement_type, "name", ""),
                getattr(movement_type, "name_en", ""),
            )
            if value
        ))
        return any(
            cls._normalized_label(token) in text
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

    @classmethod
    def _lookup_values(
        cls,
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

    @classmethod
    def _lookup_value(cls, model: Any, field: str, goal: str, fallback: str) -> str:
        """Resolve the first goal-specific prescription text with a fallback."""
        values = cls._lookup_values(model, field, goal)
        return values[0] if values else fallback
