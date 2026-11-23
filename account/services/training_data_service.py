from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext_lazy as _

from account.models import (
    WorkoutBestRecord,
    WorkoutPerformanceRecord,
    WorkoutProgram,
    WorkoutProgramDay,
    WorkoutProgramExercise,
    WorkoutSession,
    WorkoutSetResult,
)
from account.services.gym_program_service import GymProgramService
from account.services.training_periodization_service import TrainingPeriodizationService
from account.utils import normalize_digits


class TrainingDataService:
    """Validate and persist session-level and set-level workout outcomes."""

    SESSION_FIELDS = {"started_at", "duration_seconds", "difficulty", "status", "notes", "sets"}
    SET_FIELDS = {
        "program_exercise_id",
        "slot",
        "set_number",
        "actual_reps",
        "actual_load_kg",
        "duration_seconds",
        "distance_m",
        "average_heart_rate_bpm",
        "peak_heart_rate_bpm",
        "rpe",
        "rir",
        "completed",
        "is_warmup",
    }

    @staticmethod
    def _integer(value, field, *, minimum=0, maximum=1000000):
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
    def _decimal(value, field, *, minimum=Decimal("0"), maximum=Decimal("999999.99")):
        if value in (None, ""):
            return None
        try:
            parsed = Decimal(str(normalize_digits(value)).replace(",", ".").replace("٫", "."))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValidationError({field: _("مقدار باید عددی باشد.")}) from exc
        if not parsed.is_finite() or parsed < minimum or parsed > maximum:
            raise ValidationError({field: _("مقدار خارج از بازه مجاز است.")})
        return parsed

    @staticmethod
    def _parse_started_at(value):
        if not value:
            return timezone.now()
        parsed = parse_datetime(str(value))
        if parsed is None:
            raise ValidationError({"started_at": _("زمان شروع باید تاریخ‌وساعت ISO معتبر باشد.")})
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
        now = timezone.now()
        if parsed > now + timedelta(minutes=5):
            raise ValidationError({"started_at": _("زمان شروع نمی‌تواند در آینده باشد.")})
        return parsed

    @staticmethod
    def _movement_slots(item, *, program=None):
        from account.services.gym_program_service import GymProgramService

        def slot(exercise, cardio, reps, sets, rest, *, primary=False):
            active_sets = TrainingPeriodizationService.active_sets(program, sets, item=item) if program else str(sets or "")
            sets_min, sets_max = GymProgramService._range_from_text(active_sets)
            reps_min, reps_max = GymProgramService._range_from_text(reps)
            rest_min, rest_max = GymProgramService._range_from_text(rest)
            return {
                "exercise": exercise,
                "cardio_exercise": cardio,
                "warmup_sets": item.warmup_sets if primary else [],
                "planned_reps": reps,
                "planned_sets": active_sets,
                "planned_sets_min": sets_min,
                "planned_sets_max": sets_max,
                "planned_reps_min": item.reps_min if primary and item.reps_min is not None else reps_min,
                "planned_reps_max": item.reps_max if primary and item.reps_max is not None else reps_max,
                "planned_load_kg": item.target_load_kg if primary else None,
                "planned_load_percent_1rm": item.target_load_percent_1rm if primary else None,
                "planned_rpe": item.target_rpe if primary else None,
                "planned_rir": item.target_rir if primary else None,
                "planned_rest_seconds_min": item.rest_seconds_min if primary and item.rest_seconds_min is not None else rest_min,
                "planned_rest_seconds_max": item.rest_seconds_max if primary and item.rest_seconds_max is not None else rest_max,
                "planned_duration_seconds": item.cardio_duration_seconds if primary else None,
                "planned_interval_count": item.cardio_interval_count if primary else None,
                "planned_work_seconds": item.cardio_work_seconds if primary else None,
                "planned_recovery_seconds": item.cardio_recovery_seconds if primary else None,
                "target_heart_rate_min": item.target_heart_rate_min if primary else None,
                "target_heart_rate_max": item.target_heart_rate_max if primary else None,
            }

        return {
            "main": slot(item.exercise, item.cardio_exercise, item.reps, item.sets, item.rest, primary=True),
            "superset": slot(item.superset_exercise, None, item.superset_reps or item.reps, item.superset_sets or item.sets, item.superset_rest or item.rest),
            "third": slot(item.third_exercise, None, item.third_reps or item.reps, item.third_sets or item.sets, item.third_rest or item.rest),
        }

    @classmethod
    @transaction.atomic
    def create_session(cls, *, user, program, day, payload: dict, idempotency_key: str | None = None):
        if not isinstance(payload, dict):
            raise ValidationError({"body": _("بدنه درخواست باید یک شیء باشد.")})
        unknown = set(payload) - cls.SESSION_FIELDS
        if unknown:
            raise ValidationError({"fields": _("فیلدهای پشتیبانی‌نشده: %(fields)s") % {"fields": ", ".join(sorted(unknown))}})
        if program.user_id != user.pk or not program.is_published:
            raise ValidationError({"program": _("برنامه برای این کاربر در دسترس نیست.")})
        if day.program_id != program.pk:
            raise ValidationError({"program_day": _("روز انتخاب‌شده متعلق به این برنامه نیست.")})

        if idempotency_key is not None:
            idempotency_key = str(idempotency_key).strip()
            if not idempotency_key or len(idempotency_key) > 64:
                raise ValidationError({"idempotency_key": _("کلید درخواست باید بین ۱ تا ۶۴ نویسه باشد.")})
        fingerprint = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()
        if idempotency_key:
            existing = WorkoutSession.objects.filter(user=user, idempotency_key=idempotency_key).first()
            if existing:
                if existing.request_fingerprint != fingerprint:
                    raise ValidationError({"idempotency_key": _("این کلید قبلاً برای محتوای دیگری استفاده شده است.")})
                existing._idempotent_replay = True
                return existing

        rows = payload.get("sets")
        if not isinstance(rows, list) or not rows or len(rows) > 200:
            raise ValidationError({"sets": _("حداقل یک ست و حداکثر ۲۰۰ ست لازم است.")})
        items = {
            item.pk: item
            for item in WorkoutProgramExercise.objects.filter(day=day).select_related(
                "exercise", "cardio_exercise", "superset_exercise", "third_exercise"
            )
        }
        gym_service = GymProgramService()
        normalized_sets = []
        unique_slots = set()
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise ValidationError({"sets": _("هر ست باید یک شیء باشد.")})
            unknown_set_fields = set(row) - cls.SET_FIELDS
            if unknown_set_fields:
                raise ValidationError({"sets": _("فیلد ست پشتیبانی نمی‌شود: %(fields)s") % {"fields": ", ".join(sorted(unknown_set_fields))}})
            item_id = cls._integer(row.get("program_exercise_id"), f"sets.{index}.program_exercise_id", minimum=1)
            item = items.get(item_id)
            if item is None:
                raise ValidationError({"sets": _("حرکت انتخاب‌شده متعلق به روز این برنامه نیست.")})
            slot = str(row.get("slot") or "main")
            slots = cls._movement_slots(item, program=program)
            if slot not in slots:
                raise ValidationError({"sets": _("جایگاه حرکت معتبر نیست.")})
            movement = slots[slot]
            exercise = movement["exercise"]
            cardio_exercise = movement["cardio_exercise"]
            planned_reps = movement["planned_reps"]
            planned_sets = movement["planned_sets"]
            if exercise is None and cardio_exercise is None:
                raise ValidationError({"sets": _("حرکت انتخاب‌شده در این جایگاه تعریف نشده است.")})
            set_number = cls._integer(row.get("set_number"), f"sets.{index}.set_number", minimum=1, maximum=99)
            planned_sets_min, planned_sets_max = GymProgramService._range_from_text(planned_sets)
            actual_reps = cls._integer(row.get("actual_reps"), f"sets.{index}.actual_reps", maximum=1000)
            actual_load = cls._decimal(row.get("actual_load_kg"), f"sets.{index}.actual_load_kg")
            duration = cls._integer(row.get("duration_seconds"), f"sets.{index}.duration_seconds", maximum=604800)
            distance = cls._decimal(row.get("distance_m"), f"sets.{index}.distance_m", minimum=Decimal("0.01"), maximum=Decimal("1000000"))
            average_heart_rate = cls._integer(row.get("average_heart_rate_bpm"), f"sets.{index}.average_heart_rate_bpm", minimum=30, maximum=240)
            peak_heart_rate = cls._integer(row.get("peak_heart_rate_bpm"), f"sets.{index}.peak_heart_rate_bpm", minimum=30, maximum=240)
            rpe = cls._integer(row.get("rpe"), f"sets.{index}.rpe", maximum=10)
            rir = cls._integer(row.get("rir"), f"sets.{index}.rir", maximum=10)
            completed = row.get("completed", True)
            if not isinstance(completed, bool):
                raise ValidationError({f"sets.{index}.completed": _("وضعیت انجام باید درست یا نادرست باشد.")})
            is_warmup = row.get("is_warmup", False)
            if not isinstance(is_warmup, bool):
                raise ValidationError({f"sets.{index}.is_warmup": _("نوع ست باید درست یا نادرست باشد.")})
            if is_warmup:
                if slot != "main" or cardio_exercise or set_number > len(movement.get("warmup_sets") or []):
                    raise ValidationError({"sets": _("ست گرم‌کردن فقط برای ست‌های تعریف‌شدهٔ حرکت اصلی قابل ثبت است.")})
            elif set_number > (planned_sets_max or gym_service.parse_set_count(planned_sets)):
                raise ValidationError({"sets": _("شماره ست از تعداد ست‌های برنامه بیشتر است.")})
            outcome_values = (actual_reps, actual_load, duration, distance, average_heart_rate, peak_heart_rate, rpe, rir)
            if cardio_exercise and (actual_reps is not None or actual_load is not None):
                raise ValidationError({"sets": _("برای ست هوازی، تکرار و بار وزنه ثبت نکنید.")})
            if exercise and distance is not None:
                raise ValidationError({"sets": _("مسافت فقط برای نتیجهٔ هوازی قابل ثبت است.")})
            if exercise and (average_heart_rate is not None or peak_heart_rate is not None):
                raise ValidationError({"sets": _("ضربان ثبت‌شده در این بخش فقط برای نتیجهٔ هوازی پشتیبانی می‌شود.")})
            if average_heart_rate is not None and peak_heart_rate is not None and peak_heart_rate < average_heart_rate:
                raise ValidationError({"sets": _("بیشینهٔ ضربان نمی‌تواند از میانگین ضربان کمتر باشد.")})
            if not any(value is not None for value in outcome_values) and completed:
                raise ValidationError({"sets": _("برای هر ست انجام‌شده دست‌کم یک نتیجه واقعی ثبت کنید.")})
            movement_slot = "cardio" if cardio_exercise else slot
            unique_key = (item.pk, movement_slot, set_number, is_warmup)
            if unique_key in unique_slots:
                raise ValidationError({"sets": _("یک ست در این درخواست تکرار شده است.")})
            unique_slots.add(unique_key)
            normalized_sets.append({
                "item": item,
                "slot": movement_slot,
                "set_number": set_number,
                "exercise": exercise,
                "cardio_exercise": cardio_exercise,
                "planned_reps": str(planned_reps or "")[:60],
                "planned_movement_name": (cardio_exercise or exercise).name[:160],
                "planned_sets_min": movement["planned_sets_min"],
                "planned_sets_max": movement["planned_sets_max"],
                "planned_reps_min": movement["planned_reps_min"],
                "planned_reps_max": movement["planned_reps_max"],
                "planned_load_kg": movement["planned_load_kg"],
                "planned_load_percent_1rm": movement["planned_load_percent_1rm"],
                "planned_rpe": movement["planned_rpe"],
                "planned_rir": movement["planned_rir"],
                "planned_rest_seconds_min": movement["planned_rest_seconds_min"],
                "planned_rest_seconds_max": movement["planned_rest_seconds_max"],
                "planned_duration_seconds": movement["planned_duration_seconds"],
                "planned_interval_count": movement["planned_interval_count"],
                "planned_work_seconds": movement["planned_work_seconds"],
                "planned_recovery_seconds": movement["planned_recovery_seconds"],
                "target_heart_rate_min": movement["target_heart_rate_min"],
                "target_heart_rate_max": movement["target_heart_rate_max"],
                "actual_reps": actual_reps,
                "actual_load_kg": actual_load,
                "duration_seconds": duration,
                "distance_m": distance,
                "average_heart_rate_bpm": average_heart_rate,
                "peak_heart_rate_bpm": peak_heart_rate,
                "rpe": rpe,
                "rir": rir,
                "completed": completed,
                "is_warmup": is_warmup,
            })

        duration_seconds = cls._integer(payload.get("duration_seconds"), "duration_seconds", maximum=604800)
        difficulty = cls._integer(payload.get("difficulty"), "difficulty", maximum=10)
        status = str(payload.get("status") or WorkoutSession.Status.COMPLETED)
        if status not in WorkoutSession.Status.values:
            raise ValidationError({"status": _("وضعیت جلسه معتبر نیست.")})
        started_at = cls._parse_started_at(payload.get("started_at"))
        completed_at = None
        if status == WorkoutSession.Status.COMPLETED:
            completed_at = min(timezone.now(), started_at + timedelta(seconds=duration_seconds)) if duration_seconds else timezone.now()
        notes = str(payload.get("notes") or "").strip()
        if len(notes) > 4000:
            raise ValidationError({"notes": _("یادداشت حداکثر ۴۰۰۰ نویسه است.")})

        session = WorkoutSession(
            user=user,
            program=program,
            program_day=day,
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=duration_seconds,
            difficulty=difficulty,
            status=status,
            notes=notes,
            program_revision=program.revision,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
        )
        session.full_clean()
        try:
            with transaction.atomic():
                session.save()
        except IntegrityError:
            if idempotency_key:
                existing = WorkoutSession.objects.filter(user=user, idempotency_key=idempotency_key).first()
                if existing and existing.request_fingerprint == fingerprint:
                    existing._idempotent_replay = True
                    return existing
            raise
        set_records = []
        for values in normalized_sets:
            item = values.pop("item")
            result = WorkoutSetResult(session=session, program_exercise=item, **values)
            result.full_clean()
            set_records.append(result)
        WorkoutSetResult.objects.bulk_create(set_records)
        for result in set_records:
            if result.exercise_id is None or not result.actual_load_kg or result.is_warmup:
                continue
            repetitions = normalize_digits(result.planned_reps or "").strip()
            performance, created = WorkoutPerformanceRecord.objects.get_or_create(
                user=user,
                exercise_id=result.exercise_id,
                repetitions=repetitions,
                mode=WorkoutPerformanceRecord.Mode.WEIGHT,
                set_number=result.set_number,
                recorded_on=timezone.localdate(started_at),
                defaults={
                    "program": program,
                    "program_exercise": result.program_exercise,
                    "value": result.actual_load_kg,
                },
            )
            if not created and result.actual_load_kg > performance.value:
                performance.value = result.actual_load_kg
                performance.program = program
                performance.program_exercise = result.program_exercise
                performance.save(update_fields=["value", "program", "program_exercise", "updated_at"])
            best, best_created = WorkoutBestRecord.objects.get_or_create(
                user=user,
                exercise_id=result.exercise_id,
                defaults={
                    "program": program,
                    "program_exercise": result.program_exercise,
                    "value": result.actual_load_kg,
                },
            )
            if not best_created and result.actual_load_kg > best.value:
                best.value = result.actual_load_kg
                best.program = program
                best.program_exercise = result.program_exercise
                best.save(update_fields=["value", "program", "program_exercise", "recorded_at"])
        from account.services.exercise_strength_service import ExerciseStrengthService
        from account.services.progression_recommendation_service import ProgressionRecommendationService

        ExerciseStrengthService.record_session(session)
        ProgressionRecommendationService.create_for_session(session)
        return session

    @staticmethod
    def serialize_session(session):
        recommendations = getattr(session, "_progression_recommendations", None)
        if recommendations is None:
            recommendations = session.progression_recommendations.all()
        from account.services.progression_recommendation_service import ProgressionRecommendationService

        return {
            "id": session.pk,
            "program_id": session.program_id,
            "program_day_id": session.program_day_id,
            "program_revision": session.program_revision,
            "started_at": timezone.localtime(session.started_at).isoformat(),
            "completed_at": timezone.localtime(session.completed_at).isoformat() if session.completed_at else None,
            "duration_seconds": session.duration_seconds,
            "difficulty": session.difficulty,
            "status": session.status,
            "notes": session.notes,
            "sets": [
                {
                    "program_exercise_id": result.program_exercise_id,
                    "movement_id": result.cardio_exercise_id or result.exercise_id,
                    "slot": result.slot,
                    "set_number": result.set_number,
                    "planned_reps": result.planned_reps,
                    "planned_movement_name": result.planned_movement_name,
                    "planned_sets_min": result.planned_sets_min,
                    "planned_sets_max": result.planned_sets_max,
                    "planned_reps_min": result.planned_reps_min,
                    "planned_reps_max": result.planned_reps_max,
                    "planned_load_kg": str(result.planned_load_kg) if result.planned_load_kg is not None else None,
                    "planned_load_percent_1rm": str(result.planned_load_percent_1rm) if result.planned_load_percent_1rm is not None else None,
                    "planned_rpe": result.planned_rpe,
                    "planned_rir": result.planned_rir,
                    "planned_rest_seconds_min": result.planned_rest_seconds_min,
                    "planned_rest_seconds_max": result.planned_rest_seconds_max,
                    "planned_duration_seconds": result.planned_duration_seconds,
                    "planned_interval_count": result.planned_interval_count,
                    "planned_work_seconds": result.planned_work_seconds,
                    "planned_recovery_seconds": result.planned_recovery_seconds,
                    "target_heart_rate_min": result.target_heart_rate_min,
                    "target_heart_rate_max": result.target_heart_rate_max,
                    "actual_reps": result.actual_reps,
                    "actual_load_kg": str(result.actual_load_kg) if result.actual_load_kg is not None else None,
                    "duration_seconds": result.duration_seconds,
                    "distance_m": str(result.distance_m) if result.distance_m is not None else None,
                    "average_heart_rate_bpm": result.average_heart_rate_bpm,
                    "peak_heart_rate_bpm": result.peak_heart_rate_bpm,
                    "rpe": result.rpe,
                    "rir": result.rir,
                    "completed": result.completed,
                    "is_warmup": result.is_warmup,
                }
                for result in session.sets.all()
            ],
            "progression_recommendations": [
                ProgressionRecommendationService.serialize(item)
                for item in recommendations
            ],
        }
