from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Prefetch, QuerySet
from django.utils.translation import gettext_lazy as _

from account.domain.exercise_goals import goal_lookup_aliases
from account.utils import normalize_digits
from account.services.notification_service import NotificationService
from account.services.exercise_goal_service import ExerciseGoalService
from account.models import (
    CardioExercise,
    CorrectiveExercise,
    Exercise,
    ExerciseGoal,
    ExerciseRepetitionType,
    ExerciseRestType,
    ExerciseSetType,
    WorkoutProgram,
    WorkoutProgramCorrective,
    WorkoutProgramDay,
    WorkoutProgramExercise,
)


class GymProgramService:
    """Validate, persist, and serialize database-backed workout programs.

    The payload shape and validation rules are intentionally explicit because
    this service is the boundary for admin-authored training prescriptions.
    Keep changes here behavioural and covered by the gym-program test module.
    """

    MAX_DAYS = 14
    MAX_ITEMS_PER_DAY = 50
    MAX_CORRECTIVES = 50

    @staticmethod
    def parse_set_count(value: str | None) -> int:
        """Return the first positive set count from Persian or Latin text."""
        normalized = normalize_digits(value)
        match = re.search(r"\d+", normalized)
        if not match:
            return 1
        try:
            return max(1, int(match.group()))
        except ValueError:
            return 1

    def get_program_queryset(self) -> QuerySet[WorkoutProgram]:
        """Return programs with their nested days, exercises, and correctives prefetched."""
        return (
            WorkoutProgram.objects.select_related("user", "prescribed_by")
            .prefetch_related(
                Prefetch(
                    "days",
                    queryset=WorkoutProgramDay.objects.select_related("progress").prefetch_related(
                        Prefetch(
                            "items",
                            queryset=WorkoutProgramExercise.objects.select_related(
                                "exercise__pressure_type",
                                "cardio_exercise",
                                "superset_exercise__pressure_type",
                                "third_exercise__pressure_type",
                            ),
                        )
                    ),
                ),
                Prefetch(
                    "corrective_items",
                    queryset=WorkoutProgramCorrective.objects.select_related("corrective_exercise"),
                ),
            )
        )

    def serialize_program(self, program: WorkoutProgram | None) -> dict[str, Any]:
        """Convert a prefetched program into the JSON-compatible editor payload."""
        if program is None:
            return {"days": [], "correctives": []}

        days = []
        for day in program.days.all():
            days.append(
                {
                    "name": day.name,
                    "notes": day.notes,
                    "scheduled_weekday": day.scheduled_weekday,
                    "estimated_duration_minutes": day.estimated_duration_minutes,
                    "items": [
                        {
                            "exercise": (
                                item.cardio_exercise_id
                                if item.cardio_exercise_id
                                else item.exercise_id
                            ),
                            "exercise_type": "cardio" if item.cardio_exercise_id else "strength",
                            "body_part_filter": "cardio" if item.cardio_exercise_id else "",
                            "exercise_name": item.movement.name,
                            "superset_exercise": item.superset_exercise_id,
                            "superset_exercise_name": (
                                item.superset_exercise.name if item.superset_exercise_id else ""
                            ),
                            "third_exercise": item.third_exercise_id,
                            "third_exercise_name": (
                                item.third_exercise.name if item.third_exercise_id else ""
                            ),
                            "goal": item.goal,
                            "sets": item.sets,
                            "reps": item.reps,
                            "rest": item.rest,
                            "superset_sets": item.superset_sets,
                            "superset_reps": item.superset_reps,
                            "superset_rest": item.superset_rest,
                            "third_sets": item.third_sets,
                            "third_reps": item.third_reps,
                            "third_rest": item.third_rest,
                            "note": item.note,
                            "selection_reason": item.selection_reason,
                            "selection_score": item.selection_score,
                            "warmup_sets": item.warmup_sets or [],
                            "previous_performance": item.previous_performance_snapshot or {},
                            "weekly_set_targets": item.weekly_set_targets or {},
                            "prescription": {
                                "sets_min": item.sets_min,
                                "sets_max": item.sets_max,
                                "reps_min": item.reps_min,
                                "reps_max": item.reps_max,
                                "load_kg": str(item.target_load_kg) if item.target_load_kg is not None else None,
                                "load_percent_1rm": str(item.target_load_percent_1rm) if item.target_load_percent_1rm is not None else None,
                                "rpe": item.target_rpe,
                                "rir": item.target_rir,
                                "rest_seconds_min": item.rest_seconds_min,
                                "rest_seconds_max": item.rest_seconds_max,
                                "duration_seconds": item.cardio_duration_seconds,
                                "interval_count": item.cardio_interval_count,
                                "work_seconds": item.cardio_work_seconds,
                                "recovery_seconds": item.cardio_recovery_seconds,
                                "heart_rate_min": item.target_heart_rate_min,
                                "heart_rate_max": item.target_heart_rate_max,
                            },
                        }
                        for item in day.items.all()
                    ],
                }
            )

        correctives = [
            {
                "corrective_exercise": item.corrective_exercise_id,
                "corrective_exercise_name": item.corrective_exercise.name,
                "phase": item.phase,
                "sets": item.sets,
                "reps": item.reps,
                "note": item.note,
            }
            for item in program.corrective_items.all()
        ]
        return {
            "days": days,
            "correctives": correctives,
            "revision": program.revision,
            "algorithm_version": program.algorithm_version,
            "generation_seed": program.generation_seed,
            "generation_metadata": program.generation_metadata,
        }

    @transaction.atomic
    def save_program(
        self,
        *,
        program_form: Any,
        target_user: Any,
        prescribed_by: Any,
        days_payload: list[dict[str, Any]],
        correctives_payload: list[dict[str, Any]] | None = None,
        generation_metadata: dict[str, Any] | None = None,
    ) -> WorkoutProgram:
        """Validate and atomically save an admin-authored workout program."""
        days_payload = days_payload or []
        correctives_payload = correctives_payload or []
        self._validate_payload_shape(days_payload, correctives_payload)

        exercise_ids: set[int] = set()
        cardio_exercise_ids: set[int] = set()
        corrective_ids: set[int] = set()
        for day in days_payload:
            for item in day.get("items", []):
                exercise_type = str(item.get("exercise_type") or "strength").strip().lower()
                if exercise_type == "cardio":
                    if item.get("superset_exercise") not in (None, "", 0, "0") or item.get("third_exercise") not in (None, "", 0, "0"):
                        raise ValidationError(_("حرکت هوازی را نمی‌توان در سوپرست یا حرکت سوم قرار داد."))
                    cardio_exercise_ids.add(
                        self._parse_id(item.get("exercise"), _("حرکت هوازی"))
                    )
                elif exercise_type == "strength":
                    exercise_ids.add(self._parse_id(item.get("exercise"), _("حرکت اصلی")))
                else:
                    raise ValidationError(_("نوع حرکت برنامه معتبر نیست."))
                if item.get("superset_exercise") not in (None, "", 0, "0"):
                    exercise_ids.add(self._parse_id(item.get("superset_exercise"), _("حرکت دوم سوپرست")))
                if item.get("third_exercise") not in (None, "", 0, "0"):
                    exercise_ids.add(self._parse_id(item.get("third_exercise"), _("حرکت سوم")))
        for item in correctives_payload:
            corrective_ids.add(
                self._parse_id(item.get("corrective_exercise"), _("حرکت اصلاحی"))
            )

        exercises = {
            exercise.pk: exercise
            for exercise in Exercise.objects.filter(pk__in=exercise_ids)
        }
        cardio_exercises = {
            exercise.pk: exercise
            for exercise in CardioExercise.objects.filter(pk__in=cardio_exercise_ids)
        }
        correctives = {
            corrective.pk: corrective
            for corrective in CorrectiveExercise.objects.filter(pk__in=corrective_ids)
        }

        missing_exercises = exercise_ids - exercises.keys()
        if missing_exercises:
            raise ValidationError(_("یکی از حرکت‌های انتخاب‌شده در کتابخانه وجود ندارد."))
        if cardio_exercise_ids - cardio_exercises.keys():
            raise ValidationError(_("یکی از حرکت‌های هوازی انتخاب‌شده در کتابخانه وجود ندارد."))
        missing_correctives = corrective_ids - correctives.keys()
        if missing_correctives:
            raise ValidationError(_("یکی از حرکت‌های اصلاحی انتخاب‌شده در کتابخانه وجود ندارد."))

        program = program_form.save(commit=False)
        is_new = program.pk is None
        was_published = False
        if not is_new:
            was_published = bool(
                WorkoutProgram.objects.filter(pk=program.pk).values_list("is_published", flat=True).first()
            )
        program.revision = 1 if is_new else (program.revision or 1) + 1
        if generation_metadata is not None:
            if not isinstance(generation_metadata, dict):
                raise ValidationError(_("اطلاعات مبنای تولید برنامه معتبر نیست."))
            program.generation_metadata = generation_metadata
            program.algorithm_version = str(generation_metadata.get("algorithm_version") or "")[:32]
            program.generation_seed = str(generation_metadata.get("seed") or "")[:64]
        program.user = target_user
        program.prescribed_by = prescribed_by
        program.full_clean()
        program.save()

        program.days.all().delete()
        program.corrective_items.all().delete()

        for day_order, day_data in enumerate(days_payload, start=1):
            day = WorkoutProgramDay.objects.create(
                program=program,
                name=self._text(day_data.get("name"), _("عنوان روز"), required=True, max_length=80),
                notes=self._text(day_data.get("notes"), _("توضیحات روز"), max_length=5000),
                scheduled_weekday=self._weekday(day_data.get("scheduled_weekday")),
                estimated_duration_minutes=self._optional_bound(
                    day_data.get("estimated_duration_minutes"),
                    "estimated_duration_minutes",
                    1,
                    600,
                ),
                order=day_order,
            )
            for item_order, item_data in enumerate(day_data.get("items", []), start=1):
                exercise_type = str(item_data.get("exercise_type") or "strength").strip().lower()
                exercise_id = None
                cardio_exercise_id = None
                if exercise_type == "cardio":
                    cardio_exercise_id = self._parse_id(item_data.get("exercise"), _("حرکت هوازی"))
                else:
                    exercise_id = self._parse_id(item_data.get("exercise"), _("حرکت اصلی"))
                superset_id = item_data.get("superset_exercise")
                superset_id = (
                    self._parse_id(superset_id, _("حرکت دوم سوپرست"))
                    if superset_id not in (None, "", 0, "0")
                    else None
                )
                third_id = item_data.get("third_exercise")
                third_id = (
                    self._parse_id(third_id, _("حرکت سوم"))
                    if third_id not in (None, "", 0, "0")
                    else None
                )
                if superset_id == exercise_id:
                    raise ValidationError(_("حرکت دوم سوپرست باید با حرکت اصلی متفاوت باشد."))
                if third_id == exercise_id:
                    raise ValidationError(_("حرکت سوم باید با حرکت اصلی متفاوت باشد."))
                if third_id and third_id == superset_id:
                    raise ValidationError(_("حرکت سوم باید با حرکت دوم متفاوت باشد."))
                goal = self._normalize_goal(item_data.get("goal"))
                self._validate_lookup_value(ExerciseSetType, "set_count", item_data.get("sets"), goal)
                self._validate_lookup_value(ExerciseRepetitionType, "reps", item_data.get("reps"), goal)
                self._validate_lookup_value(ExerciseRestType, "rest_time", item_data.get("rest"), goal)
                warmup_payload = item_data.get("warmup_sets") or []
                if not isinstance(warmup_payload, list) or len(warmup_payload) > 3:
                    raise ValidationError(_("حداکثر سه ست گرم‌کردن برای هر حرکت قابل ثبت است."))
                warmup_sets = []
                for warmup in warmup_payload:
                    if not isinstance(warmup, dict) or warmup.get("percent") not in (50, 70, 85):
                        raise ValidationError(_("ساختار ست گرم‌کردن معتبر نیست."))
                    warmup_sets.append({
                        "percent": warmup["percent"],
                        "load_kg": str(self._optional_decimal(warmup.get("load_kg"), "warmup_load_kg", Decimal("0.01"), Decimal("999999.99")) or ""),
                        "reps": self._optional_bound(warmup.get("reps", 5), "warmup_reps", 1, 50) or 5,
                    })
                previous_payload = item_data.get("previous_performance") or {}
                if not isinstance(previous_payload, dict):
                    raise ValidationError(_("خلاصه عملکرد قبلی معتبر نیست."))
                previous_performance = {
                    key: previous_payload.get(key)
                    for key in ("date", "load_kg", "reps", "rpe", "rir")
                    if previous_payload.get(key) not in (None, "")
                }
                weekly_payload = item_data.get("weekly_set_targets") or {}
                if not isinstance(weekly_payload, dict) or len(weekly_payload) > 18:
                    raise ValidationError(_("تعداد ست هفتگی معتبر نیست."))
                weekly_set_targets = {}
                for week, count in weekly_payload.items():
                    week_number = self._optional_bound(week, "weekly_set_targets.week", 1, 18)
                    low, high = self._range_from_text(str(count))
                    if week_number is None or low is None or low < 1 or high > 99:
                        raise ValidationError(_("بازه ست هفتگی معتبر نیست."))
                    weekly_set_targets[str(week_number)] = str(count)
                program_item = WorkoutProgramExercise(
                    day=day,
                    exercise=exercises.get(exercise_id) if exercise_id else None,
                    cardio_exercise=(
                        cardio_exercises.get(cardio_exercise_id)
                        if cardio_exercise_id
                        else None
                    ),
                    superset_exercise=exercises.get(superset_id) if superset_id else None,
                    third_exercise=exercises.get(third_id) if third_id else None,
                    goal=goal,
                    sets=self._text(item_data.get("sets"), _("تعداد ست"), required=True, max_length=40),
                    reps=self._text(
                        item_data.get("reps"),
                        _("تعداد تکرار"),
                        required=not bool(cardio_exercise_id),
                        max_length=60,
                    ),
                    rest=self._text(item_data.get("rest"), _("استراحت"), max_length=40),
                    superset_sets=(
                        self._optional_movement_text(
                            item_data,
                            "superset_sets",
                            "sets",
                            _("تعداد ست حرکت دوم"),
                            40,
                        )
                        if superset_id
                        else None
                    ),
                    superset_reps=(
                        self._optional_movement_text(
                            item_data,
                            "superset_reps",
                            "reps",
                            _("تعداد تکرار حرکت دوم"),
                            60,
                        )
                        if superset_id
                        else None
                    ),
                    superset_rest=(
                        self._optional_movement_text(
                            item_data,
                            "superset_rest",
                            "rest",
                            _("استراحت حرکت دوم"),
                            40,
                        )
                        if superset_id
                        else None
                    ),
                    third_sets=(
                        self._optional_movement_text(
                            item_data,
                            "third_sets",
                            "sets",
                            _("تعداد ست حرکت سوم"),
                            40,
                        )
                        if third_id
                        else None
                    ),
                    third_reps=(
                        self._optional_movement_text(
                            item_data,
                            "third_reps",
                            "reps",
                            _("تعداد تکرار حرکت سوم"),
                            60,
                        )
                        if third_id
                        else None
                    ),
                    third_rest=(
                        self._optional_movement_text(
                            item_data,
                            "third_rest",
                            "rest",
                            _("استراحت حرکت سوم"),
                            40,
                        )
                        if third_id
                        else None
                    ),
                    note=self._text(item_data.get("note"), _("نکته"), max_length=5000),
                    sets_min=self._optional_bound(item_data.get("sets_min"), "sets_min", 1, 99)
                    if item_data.get("sets_min") not in (None, "") else self._range_from_text(item_data.get("sets"))[0],
                    sets_max=self._optional_bound(item_data.get("sets_max"), "sets_max", 1, 99)
                    if item_data.get("sets_max") not in (None, "") else self._range_from_text(item_data.get("sets"))[1],
                    reps_min=self._optional_bound(item_data.get("reps_min"), "reps_min", 1, 1000)
                    if item_data.get("reps_min") not in (None, "") else self._range_from_text(item_data.get("reps"))[0],
                    reps_max=self._optional_bound(item_data.get("reps_max"), "reps_max", 1, 1000)
                    if item_data.get("reps_max") not in (None, "") else self._range_from_text(item_data.get("reps"))[1],
                    target_load_kg=self._optional_decimal(item_data.get("target_load_kg"), "target_load_kg", Decimal("0.01"), Decimal("999999.99")),
                    target_load_percent_1rm=self._optional_decimal(item_data.get("target_load_percent_1rm"), "target_load_percent_1rm", Decimal("0.01"), Decimal("150")),
                    target_rpe=self._optional_bound(item_data.get("target_rpe"), "target_rpe", 0, 10),
                    target_rir=self._optional_bound(item_data.get("target_rir"), "target_rir", 0, 10),
                    warmup_sets=warmup_sets,
                    previous_performance_snapshot=previous_performance,
                    weekly_set_targets=weekly_set_targets,
                    rest_seconds_min=self._optional_bound(item_data.get("rest_seconds_min"), "rest_seconds_min", 0, 604800)
                    if item_data.get("rest_seconds_min") not in (None, "") else self._range_from_text(item_data.get("rest"))[0],
                    rest_seconds_max=self._optional_bound(item_data.get("rest_seconds_max"), "rest_seconds_max", 0, 604800)
                    if item_data.get("rest_seconds_max") not in (None, "") else self._range_from_text(item_data.get("rest"))[1],
                    cardio_duration_seconds=self._optional_bound(item_data.get("cardio_duration_seconds"), "cardio_duration_seconds", 1, 604800),
                    cardio_interval_count=self._optional_bound(item_data.get("cardio_interval_count"), "cardio_interval_count", 1, 1000),
                    cardio_work_seconds=self._optional_bound(item_data.get("cardio_work_seconds"), "cardio_work_seconds", 1, 604800),
                    cardio_recovery_seconds=self._optional_bound(item_data.get("cardio_recovery_seconds"), "cardio_recovery_seconds", 0, 604800),
                    target_heart_rate_min=self._optional_bound(item_data.get("target_heart_rate_min"), "target_heart_rate_min", 30, 240),
                    target_heart_rate_max=self._optional_bound(item_data.get("target_heart_rate_max"), "target_heart_rate_max", 30, 240),
                    selection_reason=self._text(item_data.get("selection_reason"), _("دلیل انتخاب حرکت"), max_length=2000),
                    selection_score=self._optional_bound(item_data.get("selection_score"), "selection_score", 0, 100),
                    order=item_order,
                )
                program_item.full_clean()
                program_item.save()

        for item_order, item_data in enumerate(correctives_payload, start=1):
            phase = self._text(item_data.get("phase"), _("مرحله اجرا"), required=True, max_length=20)
            if phase not in WorkoutProgramCorrective.Phase.values:
                raise ValidationError(_("مرحله حرکت اصلاحی معتبر نیست."))
            corrective_id = self._parse_id(item_data.get("corrective_exercise"), _("حرکت اصلاحی"))
            WorkoutProgramCorrective.objects.create(
                program=program,
                corrective_exercise=correctives[corrective_id],
                phase=phase,
                sets=self._text(item_data.get("sets"), _("تعداد ست"), max_length=40),
                reps=self._text(item_data.get("reps"), _("تکرار/مدت"), max_length=60),
                note=self._text(item_data.get("note"), _("نکته"), max_length=5000),
                order=item_order,
            )

        if program.is_published and (is_new or not was_published):
            NotificationService.schedule_program_registered(program)
        return program

    def _validate_payload_shape(
        self,
        days_payload: list[dict[str, Any]],
        correctives_payload: list[dict[str, Any]],
    ) -> None:
        """Validate collection sizes and nested shapes before touching the database."""
        if not days_payload:
            raise ValidationError(_("حداقل یک روز برای برنامه اضافه کنید."))
        if len(days_payload) > self.MAX_DAYS:
            raise ValidationError(_("تعداد روزهای برنامه بیش از حد مجاز است."))

        total_items = 0
        for day in days_payload:
            if not isinstance(day, dict):
                raise ValidationError(_("ساختار یکی از روزهای برنامه معتبر نیست."))
            items = day.get("items")
            if not isinstance(items, list) or not items:
                raise ValidationError(_("هر روز برنامه باید حداقل یک حرکت داشته باشد."))
            if len(items) > self.MAX_ITEMS_PER_DAY:
                raise ValidationError(_("تعداد حرکت‌های یک روز بیش از حد مجاز است."))
            total_items += len(items)
            if not all(isinstance(item, dict) for item in items):
                raise ValidationError(_("ساختار حرکت‌های برنامه معتبر نیست."))
        if total_items == 0:
            raise ValidationError(_("حداقل یک حرکت برای برنامه اضافه کنید."))
        if len(correctives_payload) > self.MAX_CORRECTIVES:
            raise ValidationError(_("تعداد حرکت‌های اصلاحی بیش از حد مجاز است."))
        if not all(isinstance(item, dict) for item in correctives_payload):
            raise ValidationError(_("ساختار حرکت‌های اصلاحی معتبر نیست."))

    @staticmethod
    def _parse_id(value: Any, label: Any) -> int:
        """Parse a positive catalog primary key or raise a localized validation error."""
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            raise ValidationError(_("%(label)s انتخاب‌شده معتبر نیست.") % {"label": label})
        if parsed <= 0:
            raise ValidationError(_("%(label)s انتخاب‌شده معتبر نیست.") % {"label": label})
        return parsed

    @staticmethod
    def _weekday(value: Any) -> str:
        value = str(value or "").strip()
        if value not in WorkoutProgramDay.Weekday.values:
            if value:
                raise ValidationError(_("روز پیشنهادی هفته معتبر نیست."))
            return ""
        return value

    @staticmethod
    def _optional_bound(value: Any, field: str, minimum: int, maximum: int) -> int | None:
        if value in (None, ""):
            return None
        try:
            parsed = int(normalize_digits(value))
        except (TypeError, ValueError) as exc:
            raise ValidationError({field: _("مقدار باید عدد صحیح باشد.")}) from exc
        if not minimum <= parsed <= maximum:
            raise ValidationError({field: _("مقدار خارج از بازه مجاز است.")})
        return parsed

    @staticmethod
    def _optional_decimal(value: Any, field: str, minimum: Decimal, maximum: Decimal) -> Decimal | None:
        if value in (None, ""):
            return None
        try:
            parsed = Decimal(str(normalize_digits(value)).replace(",", ".").replace("٫", "."))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValidationError({field: _("مقدار باید عددی باشد.")}) from exc
        if not parsed.is_finite() or not minimum <= parsed <= maximum:
            raise ValidationError({field: _("مقدار خارج از بازه مجاز است.")})
        return parsed

    @staticmethod
    def _range_from_text(value: Any) -> tuple[int | None, int | None]:
        """Extract only explicit exact/ranged numeric prescriptions from legacy text."""
        normalized = str(normalize_digits(value or "")).strip()
        match = re.fullmatch(r"\s*(\d{1,6})\s*(?:[-–—]|تا|to)\s*(\d{1,6})(?:\s*[^\d]*)?", normalized, re.IGNORECASE)
        if match:
            low, high = int(match.group(1)), int(match.group(2))
            if high >= low:
                return low, high
            return None, None
        match = re.fullmatch(r"\s*(\d{1,6})(?:\s*[^\d]*)?", normalized)
        if match:
            value = int(match.group(1))
            return value, value
        return None, None

    @staticmethod
    def _text(
        value: Any,
        label: Any,
        *,
        required: bool = False,
        max_length: int | None = None,
    ) -> str:
        """Normalize and validate a bounded text field from an editor payload."""
        value = str(value or "").strip()
        if required and not value:
            raise ValidationError(_("%(label)s را وارد کنید.") % {"label": label})
        if max_length and len(value) > max_length:
            raise ValidationError(
                _("%(label)s نباید بیشتر از %(length)s کاراکتر باشد.")
                % {"label": label, "length": max_length}
            )
        return value

    @classmethod
    def _optional_movement_text(
        cls,
        item_data: dict[str, Any],
        key: str,
        fallback_key: str,
        label: Any,
        max_length: int,
    ) -> str:
        """Read an optional superset/triset field, falling back to the main value."""
        value = item_data.get(key)
        if value is None:
            value = item_data.get(fallback_key)
        return cls._text(value, label, max_length=max_length)

    @staticmethod
    def _normalize_goal(value: Any) -> str:
        """Store the stable database key while accepting legacy Persian values."""
        value = str(value or "").strip()
        if not value:
            return ""
        return ExerciseGoalService.canonical_key(value)[:80]

    @classmethod
    def _validate_lookup_value(cls, model, field: str, value: Any, goal: str) -> None:
        if not goal or not str(value or "").strip():
            return
        goal_key = ExerciseGoalService.canonical_key(goal)
        goal_obj = ExerciseGoal.objects.filter(name_en__iexact=goal_key).order_by("pk").first()
        if goal_obj is None:
            return
        aliases = cls._goal_aliases(goal_obj)
        lookup = model.objects.filter(**{f"{field}__iexact": str(value).strip()})
        if not any((item.goal or "").strip().lower() in {alias.lower() for alias in aliases} for item in lookup):
            raise ValidationError(_("مقدار %(field)s با هدف انتخاب‌شده سازگار نیست.") % {"field": field})

    @staticmethod
    def _goal_aliases(goal_obj) -> set[str]:
        key = ExerciseGoalService.canonical_key(goal_obj.name_en)
        return set(goal_lookup_aliases(key)) | {
            goal_obj.name_en,
            goal_obj.name,
        }
