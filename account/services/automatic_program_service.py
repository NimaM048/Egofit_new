"""Orchestrate automatic strength and cardio program previews."""

from __future__ import annotations

import hashlib
import json
import random
from typing import Any, Iterable, Mapping, Sequence

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from account.domain.exercise_goals import canonical_goal_key
from account.models import (
    CorrectiveExercise,
    ExerciseDifficultyLevel,
    ExerciseTrainingMethod,
    ProgramTemplate,
    WorkoutProgramDay,
)
from account.services.training_adherence_service import TrainingAdherenceService
from account.services.program_generation import ALGORITHM_VERSION
from account.services.program_generation.catalog import (
    GOAL_ALIASES,
    build_session_targets,
    program_goal_for_training_goal,
    _library_fingerprint,
    _is_cardio_goal,
    _label_contains,
    _difficulty_rank,
    _pick_varied_exercise,
    _session_plans,
)
from account.services.program_generation.athlete import (
    _training_profile,
    _readiness_snapshot,
    _approved_progression_loads,
    _approved_deload_recommendations,
    _approved_rep_progressions,
    _exercise_preferences,
    _muscle_fatigue_snapshot,
    _last_performance_snapshot,
    AthleteEvidence,
)
from account.services.program_generation.prescriptions import (
    _format_set_range,
    _prescription_catalogs,
)
from account.services.program_generation.workload import (
    _weekly_muscle_volume,
    _weekly_volume_target,
    _apply_weekly_volume_progression,
)
from account.services.program_generation.cardio import generate_cardio
from account.services.program_generation.strength_catalog import (
    build_target_maps,
    load_strength_catalog,
)
from account.services.program_generation.strength_days import build_strength_days


class AutomaticProgramService:
    """Build an editable program preview using the exercise library."""

    ALGORITHM_VERSION = ALGORITHM_VERSION
    GOAL_ALIASES = GOAL_ALIASES

    build_session_targets = staticmethod(build_session_targets)
    program_goal_for_training_goal = staticmethod(program_goal_for_training_goal)
    _is_cardio_goal = staticmethod(_is_cardio_goal)
    _readiness_snapshot = staticmethod(_readiness_snapshot)
    _muscle_fatigue_snapshot = staticmethod(_muscle_fatigue_snapshot)
    _exercise_preferences = staticmethod(_exercise_preferences)
    _last_performance_snapshot = staticmethod(_last_performance_snapshot)
    _approved_progression_loads = staticmethod(_approved_progression_loads)
    _approved_deload_recommendations = staticmethod(_approved_deload_recommendations)
    _approved_rep_progressions = staticmethod(_approved_rep_progressions)
    _library_fingerprint = staticmethod(_library_fingerprint)
    _weekly_volume_target = staticmethod(_weekly_volume_target)
    _apply_weekly_volume_progression = staticmethod(_apply_weekly_volume_progression)
    _pick_varied_exercise = staticmethod(_pick_varied_exercise)

    @staticmethod
    def _resolve_seed(seed: str | int | None, **context: Any) -> str:
        if seed not in (None, ""):
            value = str(seed).strip()
            if len(value) > 64:
                raise ValidationError({"seed": _("بذر تولید نباید بیش از ۶۴ نویسه باشد.")})
            return value
        stable_context = json.dumps(context, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(stable_context.encode("utf-8")).hexdigest()[:24]

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
        profile = _training_profile(athlete)
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
        mapped_training_goal = program_goal_for_training_goal(selected_training_goal)
        if mapped_training_goal and not _is_cardio_goal(goal):
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
        readiness = _readiness_snapshot(athlete_id)
        adherence = TrainingAdherenceService.snapshot(athlete) if athlete_id else {"available": False, "percent": None, "low": False}
        requested_sessions = sessions
        sessions = TrainingAdherenceService.effective_sessions(sessions, adherence)
        approved_loads = _approved_progression_loads(athlete_id)
        approved_deloads = _approved_deload_recommendations(athlete_id)
        approved_rep_progressions = _approved_rep_progressions(athlete_id)
        preferences = _exercise_preferences(athlete_id)
        fatigue = _muscle_fatigue_snapshot(
            athlete_id,
            profile.experience_years if profile else None,
        )
        fatigue_scores = {muscle_id: value["fatigue"] for muscle_id, value in fatigue.items()}
        last_performance = _last_performance_snapshot(athlete_id)
        seed_value = self._resolve_seed(
            seed,
            algorithm_version=ALGORITHM_VERSION,
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
        if _is_cardio_goal(goal):
            return generate_cardio(
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

        selected_ids, selected_pattern_ids, movement_type_map, pattern_type_map = build_target_maps(
            secondary_movement_counts, session_movement_targets,
        )

        prescription_catalogs = _prescription_catalogs(
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
            method_catalogs[method] = _prescription_catalogs(goal, method)
        catalog = load_strength_catalog(
            difficulty=difficulty,
            abnormalities=abnormalities,
            selected_equipment_ids=selected_equipment_ids,
            selected_ids=selected_ids,
            selected_pattern_ids=selected_pattern_ids,
            filter_by_equipment=filter_by_equipment,
            preferences=preferences,
        )

        session_plans = _session_plans(
            sessions=sessions,
            selected_ids=selected_ids,
            secondary_movement_counts=secondary_movement_counts,
            session_movement_targets=session_movement_targets,
            movement_type_map=movement_type_map,
            pattern_type_map=pattern_type_map,
        )
        plan = build_strength_days(
            catalog=catalog,
            evidence=AthleteEvidence(
                readiness=readiness,
                fatigue_scores=fatigue_scores,
                preferences=preferences,
                last_performance=last_performance,
                approved_loads=approved_loads,
                approved_deloads=approved_deloads,
                approved_rep_progressions=approved_rep_progressions,
            ),
            session_plans=session_plans,
            movement_type_map=movement_type_map,
            pattern_type_map=pattern_type_map,
            profile=profile,
            goal=goal,
            movements=movements,
            equipment_known=bool(selected_equipment_ids),
            prescription_catalogs=prescription_catalogs,
            method_catalogs=method_catalogs,
            has_explicit_session_targets=bool(session_movement_targets),
            has_explicit_movement_counts=isinstance(secondary_movement_counts, dict),
            rng=rng,
            seed_offset=seed_offset,
        )
        days = plan.days

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
            profile_goal_alias = program_goal_for_training_goal(selected_training_goal)
            if profile_goal_alias and not _label_contains(goal, GOAL_ALIASES.get(profile_goal_alias, (profile_goal_alias,))):
                profile_warnings.append(_("هدف برنامه با هدف اصلی انتخاب‌شده فرق دارد؛ بازبینی مربی پیشنهاد می‌شود."))
        if profile:
            if profile.experience_years is not None and profile.experience_years <= 1:
                if _difficulty_rank(ExerciseDifficultyLevel.objects.filter(pk=catalog.difficulty_id).first()) not in (None, 0):
                    profile_warnings.append(_("سابقه ثبت‌شده حداکثر یک سال است، اما سطح دشواری بالاتر انتخاب شده؛ مربی پیش از انتشار بررسی کند."))
            if profile.sessions_per_week and profile.sessions_per_week != sessions:
                profile_warnings.append(_("تعداد جلسه‌های این برنامه با تعداد ثبت‌شده در پروفایل تفاوت دارد؛ این انتخاب ادمین را بازبینی کنید."))
        if abnormality_ids:
            profile_warnings.append(_("محدودیت‌های ثبت‌شده فقط با مواردی که مربی در کتابخانه به حرکت پیوند داده فیلتر شدند؛ بازبینی مربی لازم است."))
        if catalog.excluded_by_equipment:
            profile_warnings.append(_("%(count)s حرکت به‌دلیل تجهیزات در دسترس حذف شد.") % {"count": catalog.excluded_by_equipment})
        if catalog.excluded_by_restriction:
            profile_warnings.append(_("%(count)s حرکت به‌دلیل محدودیت نشانه‌گذاری‌شده در کتابخانه حذف شد.") % {"count": catalog.excluded_by_restriction})
        weekly_muscle_volume = _weekly_muscle_volume(days, plan.exercise_by_id)
        muscles_by_name = {}
        for exercise in plan.exercise_by_id.values():
            if exercise.primary_muscle_id:
                muscles_by_name[exercise.primary_muscle.name] = exercise.primary_muscle
            if exercise.secondary_muscle_id:
                muscles_by_name[exercise.secondary_muscle.name] = exercise.secondary_muscle
        weekly_volume_targets = {
            muscle_name: _weekly_volume_target(
                mapped_training_goal or goal,
                profile,
                muscle=muscles_by_name.get(muscle_name),
                training_goal=selected_training_goal,
            )
            for muscle_name in weekly_muscle_volume
        }
        cycle_weeks = template.weeks if template else 4
        _apply_weekly_volume_progression(
            days,
            plan.exercise_by_id,
            weekly_muscle_volume,
            weekly_volume_targets,
            cycle_weeks=cycle_weeks,
        )
        weekly_muscle_volume_by_week = {
            str(week): _weekly_muscle_volume(days, plan.exercise_by_id, week=week)
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
                        "sets": _format_set_range(int(lower), int(upper)),
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
        library_fingerprint = _library_fingerprint(catalog.candidates)
        metadata = {
            "algorithm_version": ALGORITHM_VERSION,
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
            "eligible_exercise_count": len(catalog.exercises),
            "excluded_equipment_count": catalog.excluded_by_equipment,
            "excluded_restriction_count": catalog.excluded_by_restriction,
            "substitution_count": plan.substitution_count,
            "progression_recommendations_applied": len(plan.applied_progression_ids),
            "progression_recommendation_ids": sorted(plan.applied_progression_ids),
            "weekly_muscle_volume": weekly_muscle_volume,
            "weekly_muscle_volume_by_week": weekly_muscle_volume_by_week,
            "weekly_volume_targets": weekly_volume_targets,
            "weekly_volume_target": _weekly_volume_target(mapped_training_goal or goal, profile, training_goal=selected_training_goal),
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
            "human_review_required": bool(abnormality_ids) or not selected_equipment_ids or bool(readiness.get("low")) or bool(adherence.get("low")) or any(value["fatigue"] >= 75 for value in fatigue.values()) or bool(plan.duration_warnings) or bool(plan.substitution_count),
        }
        return {
            "days": days,
            "correctives": corrective_items,
            "warnings": volume_warnings + profile_warnings + plan.duration_warnings,
            "metadata": metadata,
        }
