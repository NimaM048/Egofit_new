"""Handle workout submission responses and atomic writes."""

from __future__ import annotations

import logging
import re
from decimal import Decimal
from uuid import uuid4

from django.contrib import messages
from django.core.exceptions import PermissionDenied, SuspiciousOperation, ValidationError
from django.db import transaction
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils.translation import gettext_lazy as _
from django.utils import timezone
from django.views import View

from account.models import (
    WorkoutPerformanceRecord,
    WorkoutBestRecord,
    WorkoutProgramDay,
    WorkoutProgramDayProgress,
    WorkoutProgramExercise,
    WorkoutProgramFeedback,
)
from account.portal_mixins import UserPortalRequiredMixin
from account.services.training_data_service import TrainingDataService
from account.utils import normalize_digits

from .support import gym_program_service, _has_workout_program_access
from .submission import parse_performance_value, read_session_sets, read_legacy_performance


logger = logging.getLogger("account.workouts.views")


def _wants_json_response(request) -> bool:
    return (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or "application/json" in (request.headers.get("Accept") or "")
    )


class ProfileWorkoutProgramPerformanceView(UserPortalRequiredMixin, View):
    """Save workout set values and optional difficulty feedback for the owner."""

    @staticmethod
    def _error_response(code, status, *, message=None, errors=None, reference=None):
        payload = {
            "ok": False,
            "code": code,
            "errors": errors if errors is not None else [{"field": None, "message": str(message)}],
        }
        if reference:
            payload["reference"] = reference
        return JsonResponse(payload, status=status)

    def dispatch(self, request, *args, **kwargs):
        if request.method != "POST" or not _wants_json_response(request):
            return super().dispatch(request, *args, **kwargs)
        if not request.user.is_authenticated:
            return self._error_response(
                "login_required", 401,
                message=_("نشست شما پایان یافته است. در یک زبانهٔ دیگر وارد شوید و دوباره ثبت کنید."),
            )
        try:
            response = super().dispatch(request, *args, **kwargs)
        except Http404:
            return self._error_response(
                "workout_unavailable", 404,
                message=_("این برنامه یا روز تمرین دیگر در دسترس نیست. برنامهٔ فعلی را از فهرست باز کنید."),
            )
        except (PermissionDenied, SuspiciousOperation):
            raise
        except Exception:
            reference = uuid4().hex[:12]
            logger.exception(
                "Workout submission failed reference=%s program_id=%s", reference, kwargs.get("pk"),
            )
            return self._error_response(
                "save_failed", 500,
                message=_("هنگام پردازش درخواست مشکلی رخ داد. پیش از تلاش دوباره، بررسی کنید جلسه در سوابق ثبت شده باشد."),
                reference=reference,
            )
        if response.status_code == 302:
            return self._error_response(
                "access_denied", 403, message=_("این حساب اجازهٔ ثبت نتیجه برای این برنامه را ندارد."),
            )
        return response

    def _validation_response(self, errors):
        return self._error_response(
            "validation_error", 400,
            errors=[{"field": field, "message": str(message)} for field, message in errors],
        )

    @staticmethod
    def _read_difficulty(request, errors):
        raw_value = (request.POST.get("difficulty") or "").strip()
        if not raw_value:
            return None
        try:
            difficulty = int(normalize_digits(raw_value))
            if 0 <= difficulty <= 10:
                return difficulty
        except (TypeError, ValueError):
            pass
        errors.append(("difficulty", _("سطح دشواری باید عددی بین صفر تا ۱۰ باشد.")))
        return None

    def post(self, request, pk):
        program = get_object_or_404(
            gym_program_service.get_program_queryset(),
            pk=pk,
            user=request.user,
            is_published=True,
        )
        if not _has_workout_program_access(request.user, program):
            if _wants_json_response(request):
                return self._error_response(
                    "payment_required", 403,
                    message=_("برای ثبت نتیجهٔ این برنامه، ابتدا پرداخت آن را کامل کنید."),
                )
            messages.info(request, _("برای مشاهده این برنامه ابتدا هزینه آن را پرداخت کنید."))
            return redirect("register:profile_workout_programs")
        day_id = normalize_digits((request.POST.get("day_id") or "").strip())
        selected_day = None
        if day_id:
            if not day_id.isdecimal() or len(day_id) > 19:
                raise Http404
            selected_day = get_object_or_404(
                WorkoutProgramDay,
                pk=day_id,
                program=program,
            )
            item_queryset = selected_day.items
        else:
            item_queryset = WorkoutProgramExercise.objects.filter(day__program=program)
        items = list(
            item_queryset.select_related(
                "exercise",
                "superset_exercise",
                "third_exercise",
            )
        )
        best_record_fields = {
            key: request.POST.getlist(key)
            for key in request.POST
            if key.startswith("best_record_")
        }
        if best_record_fields:
            return self._save_best_records(request, program, selected_day, items, best_record_fields)
        session_sets = read_session_sets(request.POST, items, program)
        performance_values, mode_selections, errors = read_legacy_performance(
            request.POST, request.user, items, program,
        )

        difficulty = self._read_difficulty(request, errors)

        session_duration = None
        session_duration_raw = (request.POST.get("session_duration_minutes") or "").strip()
        if session_duration_raw:
            try:
                session_duration = int(normalize_digits(session_duration_raw)) * 60
            except (TypeError, ValueError):
                errors.append(("session_duration_minutes", _("مدت جلسه باید عددی مثبت باشد.")))
            else:
                if not 1 <= session_duration <= 604800:
                    errors.append(("session_duration_minutes", _("مدت جلسه باید بین ۱ تا ۱۰۰۸۰ دقیقه باشد.")))

        if not errors and not performance_values and not session_sets and difficulty is None:
            errors.append(
                (
                    None,
                    _("هیچ رکوردی وارد نشده است. مقدار حداقل یک ست را به‌صورت عدد وارد کنید."),
                )
            )

        if errors:
            if _wants_json_response(request):
                return self._validation_response(errors)
            for _field, error in errors:
                messages.error(request, str(error))
            return redirect("register:profile_workout_programs")

        session_validation_errors = []
        with transaction.atomic():
            saved_count = 0
            if mode_selections:
                items_by_id = {item.pk: item for item in items}
                for item_id, selected_modes in mode_selections.items():
                    item = items_by_id[item_id]
                    item.performance_modes = selected_modes
                    item.save(update_fields=["performance_modes"])

            for payload in performance_values:
                lookup = {
                    "user": payload["user"],
                    "exercise": payload["exercise"],
                    "repetitions": payload["repetitions"],
                    "mode": payload["mode"],
                    "set_number": payload["set_number"],
                    "recorded_on": timezone.localdate(),
                }
                record, created = (
                    WorkoutPerformanceRecord.objects.select_for_update().get_or_create(
                        **lookup,
                        defaults={
                            "program": payload["program"],
                            "program_exercise": payload["program_exercise"],
                            "value": payload["value"],
                        },
                    )
                )
                if not created and payload["value"] > record.value:
                    record.value = payload["value"]
                    record.program = payload["program"]
                    record.program_exercise = payload["program_exercise"]
                    record.save(
                        update_fields=[
                            "value",
                            "program",
                            "program_exercise",
                            "updated_at",
                        ]
                    )
                saved_count += 1

            if difficulty is not None:
                WorkoutProgramFeedback.objects.update_or_create(
                    program=program,
                    day=selected_day,
                    defaults={"difficulty": difficulty},
                )
            if session_sets:
                try:
                    TrainingDataService.create_session(
                        user=request.user,
                        program=program,
                        day=selected_day,
                        payload={
                            "sets": session_sets,
                            "difficulty": difficulty,
                            "duration_seconds": session_duration,
                            "notes": request.POST.get("session_notes", ""),
                        },
                    )
                except ValidationError as exc:
                    transaction.set_rollback(True)
                    message_dict = getattr(exc, "message_dict", None)
                    if message_dict:
                        for source_field, field_messages in message_dict.items():
                            for message in field_messages:
                                input_field = {"notes": "session_notes", "duration_seconds": "session_duration_minutes"}.get(
                                    source_field, source_field,
                                )
                                match = re.fullmatch(
                                    r"sets\.(\d+)\.([a-z_]+)",
                                    str(source_field or ""),
                                )
                                if match:
                                    set_index = int(match.group(1))
                                    if set_index < len(session_sets):
                                        row = session_sets[set_index]
                                        input_field = (
                                            f"session_{row['program_exercise_id']}_{row['slot']}_"
                                            f"{row['set_number']}_{match.group(2)}"
                                        )
                                session_validation_errors.append(
                                    {"field": input_field, "message": str(message)}
                                )
                    else:
                        session_validation_errors.extend(
                            {"field": None, "message": str(message)}
                            for message in exc.messages
                        )
            if not session_validation_errors:
                self._mark_day_completed(selected_day, program)

        if session_validation_errors:
            if _wants_json_response(request):
                return self._validation_response(
                    (error["field"], error["message"]) for error in session_validation_errors
                )
            for error in session_validation_errors:
                messages.error(request, error["message"])
            return redirect("register:profile_workout_programs")

        if saved_count:
            messages.success(
                request,
                _("%(count)s مقدار عملکرد با موفقیت ثبت شد.")
                % {"count": saved_count},
            )
        if difficulty is not None:
            messages.success(request, _("بازخورد سطح دشواری برنامه ثبت شد."))
        if not saved_count and difficulty is None:
            messages.info(request, _("مقداری برای ثبت انتخاب نشده است."))
        if _wants_json_response(request):
            return JsonResponse(
                {
                    "ok": True,
                    "saved_count": saved_count,
                    "difficulty_saved": difficulty is not None,
                    "session_saved": bool(session_sets),
                    "message": _("جلسه و نتیجهٔ ست‌ها ثبت شد.") if session_sets else (
                        _("رکوردهای شما با موفقیت ثبت شد.") if saved_count
                        else _("بازخورد سطح دشواری ثبت شد.")
                    ),
                }
            )
        return redirect("register:profile_workout_programs")

    def _save_best_records(self, request, program, selected_day, items, fields):
        allowed_sources = {}
        for item in items:
            for exercise, reps in (
                (item.exercise, item.reps),
                (item.superset_exercise, item.superset_reps or item.reps),
                (item.third_exercise, item.third_reps or item.reps),
            ):
                if exercise is not None:
                    allowed_sources.setdefault(exercise.pk, (exercise, item, reps))

        values = {}
        errors: list[tuple[str | None, str]] = []
        difficulty = self._read_difficulty(request, errors)
        for key, raw_values in fields.items():
            try:
                exercise_id = int(key.removeprefix("best_record_"))
            except (TypeError, ValueError):
                errors.append((None, _("حرکت انتخاب‌شده معتبر نیست.")))
                continue
            if exercise_id not in allowed_sources:
                errors.append((None, _("حرکت انتخاب‌شده متعلق به این برنامه نیست.")))
                continue
            for raw_value in raw_values:
                if not (raw_value or "").strip():
                    continue
                try:
                    value = parse_performance_value(raw_value)
                except ValueError as exc:
                    exercise, _program_exercise, _reps = allowed_sources[exercise_id]
                    errors.append(
                        (
                            key,
                            _("%(exercise)s: %(error)s")
                            % {"exercise": exercise.name, "error": str(exc)},
                        )
                    )
                    continue
                values[exercise_id] = max(values.get(exercise_id, Decimal("0")), value)

        if not errors and not values:
            errors.append(
                (
                    None,
                    _("هیچ رکوردی وارد نشده است. مقدار حداقل یک حرکت را به‌صورت عدد وارد کنید."),
                )
            )

        if errors:
            if _wants_json_response(request):
                return self._validation_response(errors)
            for _field, error in errors:
                messages.error(request, str(error))
            return redirect("register:profile_workout_programs")

        saved_count = 0
        with transaction.atomic():
            for exercise_id, value in values.items():
                exercise, program_exercise, reps = allowed_sources[exercise_id]
                record, created = WorkoutBestRecord.objects.select_for_update().get_or_create(
                    user=request.user,
                    exercise=exercise,
                    defaults={
                        "program": program,
                        "program_exercise": program_exercise,
                        "value": value,
                    },
                )
                if created:
                    saved_count += 1
                elif value > record.value:
                    record.value = value
                    record.program = program
                    record.program_exercise = program_exercise
                    record.save(update_fields=["value", "program", "program_exercise", "recorded_at"])
                    saved_count += 1

                # Mirror the best lift into the performance history so the
                # analysis page can chart weight changes over time.
                repetitions = normalize_digits(reps or "").strip()
                performance, performance_created = (
                    WorkoutPerformanceRecord.objects.select_for_update().get_or_create(
                        user=request.user,
                        exercise=exercise,
                        repetitions=repetitions,
                        mode=WorkoutPerformanceRecord.Mode.WEIGHT,
                        set_number=1,
                        recorded_on=timezone.localdate(),
                        defaults={
                            "program": program,
                            "program_exercise": program_exercise,
                            "value": value,
                        },
                    )
                )
                performance_updated = False
                if not performance_created and value > performance.value:
                    performance.value = value
                    performance.program = program
                    performance.program_exercise = program_exercise
                    performance.save(
                        update_fields=[
                            "value",
                            "program",
                            "program_exercise",
                            "updated_at",
                        ]
                    )
                    performance_updated = True
                if performance_created or performance_updated:
                    saved_count = max(saved_count, 1)

            if difficulty is not None:
                WorkoutProgramFeedback.objects.update_or_create(
                    program=program,
                    day=selected_day,
                    defaults={"difficulty": difficulty},
                )
            self._mark_day_completed(selected_day, program)

        if saved_count:
            messages.success(request, _("رکوردهای جدید با موفقیت ثبت شد."))
        elif not fields:
            messages.info(request, _("مقداری برای ثبت انتخاب نشده است."))
        if _wants_json_response(request):
            return JsonResponse(
                {
                    "ok": True,
                    "saved_count": saved_count,
                    "message": _("رکوردهای شما با موفقیت ثبت شد."),
                }
            )
        return redirect("register:profile_workout_programs")

    @staticmethod
    def _mark_day_completed(day, program):
        if day is None:
            return
        progress, _ = WorkoutProgramDayProgress.objects.select_for_update().get_or_create(day=day)
        if progress.completed_count < program.duration_weeks:
            progress.completed_count += 1
            progress.save(update_fields=["completed_count", "updated_at"])
