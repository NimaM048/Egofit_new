"""Assemble catalogs and page context for automatic programming."""

from __future__ import annotations

import jdatetime
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from account.portal_mixins import is_coach_only
from account.models import (
    CardioExercise,
    CorrectiveExercise,
    Exercise,
    ExerciseBodyPart,
    AthleteTrainingProfile,
    UserHealthRecord,
)
from account.services.training_adherence_service import TrainingAdherenceService
from account.services.program_generation.athlete import _muscle_fatigue_snapshot


def build_automatic_context(request, form, preview=None, *, active_section, adherence_notification_sent=False):
    athletes = list(form.fields["user"].queryset)
    athlete_ids = [athlete.pk for athlete in athletes]
    training_profiles = {
        item.user_id: item
        for item in AthleteTrainingProfile.objects.filter(user_id__in=athlete_ids)
        .prefetch_related("available_equipment")
    }
    athlete_abnormalities = {}
    for athlete_id, abnormality_id in UserHealthRecord.objects.filter(
        user_id__in=athlete_ids,
        category=UserHealthRecord.CategoryChoices.ABNORMALITY,
        abnormality_type__isnull=False,
    ).order_by("user_id", "abnormality_type_id").values_list(
        "user_id", "abnormality_type_id"
    ).distinct():
        athlete_abnormalities.setdefault(athlete_id, []).append(abnormality_id)
    selected_athlete = form.cleaned_data.get("user") if getattr(form, "is_bound", False) and form.is_valid() else None
    selected_profile = (
        AthleteTrainingProfile.objects.filter(user=selected_athlete)
        .prefetch_related("available_equipment")
        .first()
        if selected_athlete
        else None
    )
    selected_adherence = TrainingAdherenceService.snapshot(selected_athlete) if selected_athlete else None
    preview_data = preview if isinstance(preview, dict) else {
        "days": [], "correctives": [], "metadata": {}, "token": ""
    }
    preview_adherence = (preview_data.get("metadata") or {}).get("adherence") or {}
    low_adherence = bool(
        (selected_adherence and selected_adherence.get("available")
         and selected_adherence.get("percent") is not None
         and selected_adherence["percent"] < TrainingAdherenceService.LOW_ADHERENCE_PERCENT)
        or (preview_adherence.get("available")
            and preview_adherence.get("percent") is not None
            and preview_adherence["percent"] < TrainingAdherenceService.LOW_ADHERENCE_PERCENT)
    )
    preview_warnings = list(preview_data.get("warnings") or [])
    if low_adherence:
        adherence_warning_label = str(_("پایبندی ۳۰روزه"))
        preview_warnings = [
            warning for warning in preview_warnings
            if adherence_warning_label not in str(warning)
        ]
    selected_muscle_fatigue = (
        _muscle_fatigue_snapshot(
            selected_athlete.pk,
            selected_profile.experience_years if selected_profile else None,
        )
        if selected_athlete
        else {}
    )
    exercise_catalog = []
    cardio_catalog = []
    body_part_catalog = []
    corrective_catalog = []
    if preview_data.get("days") or preview_data.get("correctives"):
        exercise_catalog = [
            {
                "id": item.pk,
                "name": item.name,
                "secondary_movement_type": item.secondary_movement_type.name if item.secondary_movement_type else "",
                "difficulty_level": item.difficulty_level.name if item.difficulty_level else "",
                "body_part_id": item.body_part_id,
                "body_part": item.body_part.name if item.body_part else "",
                "equipment_ids": sorted(
                    [required.pk for required in item.required_equipment_types.all()]
                    or ([item.equipment_type_id] if item.equipment_type_id else [])
                ),
                "excluded_abnormality_ids": sorted(
                    abnormality.pk for abnormality in item.excluded_abnormalities.all()
                ),
            }
            for item in Exercise.objects.select_related(
                "body_part", "secondary_movement_type", "difficulty_level"
            ).only(
                "pk",
                "name",
                "secondary_movement_type_id",
                "secondary_movement_type__name",
                "difficulty_level_id",
                "difficulty_level__name",
                "body_part_id",
                "body_part__name",
                "equipment_type_id",
            ).prefetch_related("required_equipment_types", "excluded_abnormalities").all()
        ]
        cardio_catalog = [
            {
                "id": item.pk,
                "name": item.name,
                "performance_type": item.performance_type,
                "performance_type_label": item.get_performance_type_display(),
                "equipment_ids": sorted(required.pk for required in item.required_equipment_types.all()),
            }
            for item in CardioExercise.objects.only(
                "pk", "name", "performance_type"
            ).prefetch_related("required_equipment_types").all()
        ]
        body_part_catalog = [
            {"id": item.pk, "name": item.name}
            for item in ExerciseBodyPart.objects.only("pk", "name")
        ]
        corrective_catalog = [
            {"id": item.pk, "name": item.name}
            for item in CorrectiveExercise.objects.only("pk", "name")
        ]
    return {
        "form": form,
        "preview": preview_data,
        "preview_warnings": preview_warnings,
        "exercise_catalog": exercise_catalog,
        "cardio_catalog": cardio_catalog,
        "body_part_catalog": body_part_catalog,
        "corrective_catalog": corrective_catalog,
        "athlete_catalog": [
            {
                "id": athlete.pk,
                "name": " ".join(
                    value.strip()
                    for value in (athlete.first_name, athlete.last_name)
                    if value and value.strip()
                ) or athlete.fullname,
                "abnormality_ids": athlete_abnormalities.get(athlete.pk, []),
                "profile": (
                    {
                        "goal": training_profiles[athlete.pk].get_primary_goal_display(),
                        "primary_goal": training_profiles[athlete.pk].primary_goal,
                        "experience_years": training_profiles[athlete.pk].experience_years,
                        "session_duration_minutes": training_profiles[athlete.pk].session_duration_minutes,
                        "preferred_days": training_profiles[athlete.pk].preferred_days or [],
                        "equipment": [item.name for item in training_profiles[athlete.pk].available_equipment.all()],
                        "equipment_ids": sorted(item.pk for item in training_profiles[athlete.pk].available_equipment.all()),
                    }
                    if athlete.pk in training_profiles
                    else None
                ),
            }
            for athlete in athletes
        ],
        "selected_profile": selected_profile,
        "selected_athlete": selected_athlete,
        "selected_adherence": selected_adherence,
        "adherence_warning_threshold": TrainingAdherenceService.LOW_ADHERENCE_PERCENT,
        "adherence_notification_sent": adherence_notification_sent,
        "selected_muscle_fatigue": selected_muscle_fatigue,
        "selected_abnormality_ids": sorted(item.pk for item in form.cleaned_data.get("abnormalities", [])) if getattr(form, "is_bound", False) and form.is_valid() else [],
        "jalali_today": jdatetime.date.fromgregorian(
            date=timezone.localdate()
        ).strftime("%Y/%m/%d"),
        "active_section": active_section,
        "is_coach_only": is_coach_only(request.user),
        "page_title": _("برنامه‌ریزی خودکار"),
        "default_start_date": (
            request.POST.get("start_date")
            or jdatetime.date.fromgregorian(date=timezone.localdate()).strftime("%Y/%m/%d")
        ),
        "posted_end_date": request.POST.get("end_date", ""),
    }
