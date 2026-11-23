"""Choose exercises and apply prescriptions to each strength session."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, ROUND_DOWN
import re
from typing import Any
from dataclasses import dataclass

from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from account.models import Exercise, ExerciseTrainingMethod
from account.utils import normalize_digits

from .catalog import (
    _selection_reason,
    _pick_varied_exercise,
    _order_session_items,
    _is_compound_exercise,
    _is_core_or_lower_back_movement_type,
)
from .prescriptions import METHOD_NOTES, _prescription_for_index
from .workload import _reduce_sets, _fit_session_duration
from .strength_catalog import StrengthCatalog
from .athlete import AthleteEvidence


@dataclass
class StrengthPlan:
    days: list[dict[str, Any]]
    exercise_by_id: dict[int, Exercise]
    substitution_count: int
    applied_progression_ids: set[int]
    duration_warnings: list[str]


def build_strength_days(
    *,
    catalog: StrengthCatalog,
    evidence: AthleteEvidence,
    session_plans,
    movement_type_map,
    pattern_type_map,
    profile,
    goal,
    movements,
    equipment_known,
    prescription_catalogs,
    method_catalogs,
    has_explicit_session_targets,
    has_explicit_movement_counts,
    rng,
    seed_offset,
) -> StrengthPlan:
    readiness = evidence.readiness
    fatigue_scores = evidence.fatigue_scores
    preferences = evidence.preferences
    last_performance = evidence.last_performance
    approved_loads = evidence.approved_loads
    approved_deloads = evidence.approved_deloads
    approved_rep_progressions = evidence.approved_rep_progressions
    difficulty_id = catalog.difficulty_id
    days = []
    exercise_by_id = {exercise.pk: exercise for exercise in catalog.exercises}
    exposure_counts = defaultdict(int)
    rotation_cursors = defaultdict(int)
    substitution_count = 0
    applied_progression_ids = set()
    applied_deload_ids = set()
    duration_warnings = []
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
            if not _is_core_or_lower_back_movement_type(pattern_type_map[slot[1]])
        ]
        finishing_pattern_slots = [
            slot for slot in pattern_slots
            if _is_core_or_lower_back_movement_type(pattern_type_map[slot[1]])
        ]
        primary_section_slots = [
            slot for slot in section_slots
            if not _is_core_or_lower_back_movement_type(movement_type_map[slot[1]])
        ]
        finishing_section_slots = [
            slot for slot in section_slots
            if _is_core_or_lower_back_movement_type(movement_type_map[slot[1]])
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
            preferred_pool = catalog.preferred_by_target.get(target_key, [])
            pool = catalog.by_target.get(target_key, [])
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
                exercise = _pick_varied_exercise(
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
                    substitute_pool = catalog.fallback_by_target.get(target_key, [])
                    exercise = _pick_varied_exercise(
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
                prescription = _prescription_for_index(
                    prescription_catalogs,
                    len(items),
                    method=ExerciseTrainingMethod.NORMAL,
                )
                reason = _selection_reason(
                    exercise=exercise,
                    target_label=target_label,
                    exact_difficulty=exercise.difficulty_level_id == difficulty_id,
                    equipment_known=equipment_known,
                    profile=profile,
                )
                if used_substitute:
                    substitution_count += 1
                    reason += " " + str(_("جایگزین هم‌عضله و هم‌الگوی حرکت انتخاب شد؛ حرکت اصلی این بخش با محدودیت تجهیزات یا ناهنجاری روبه‌رو بود."))
                elif catalog.excluded_targets.get(target_key):
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
                            + (10 if equipment_known else 0),
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

        available_methods = [method for method, values in method_catalogs.items() if values]
        if available_methods and items:
            special_count = rng.randint(1, min(2, len(items)))
            for item_index in rng.sample(range(len(items)), special_count):
                method = rng.choice(available_methods)
                prescription = _prescription_for_index(
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
                        "note": METHOD_NOTES[method],
                        "training_method": method,
                    }
                )
        items = _order_session_items(items, exercise_by_id)
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
            (item for item in items if _is_compound_exercise(exercise_by_id[item["exercise"]])),
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
                        item[field] = _reduce_sets(item[field], decrement=1)
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
                        item[field] = _reduce_sets(item[field], factor=Decimal("0.5"))
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
                    item["sets"] = _reduce_sets(item["sets"], decrement=1)
                    item["selection_reason"] = " ".join(filter(None, (item.get("selection_reason", ""), str(_("خستگی عضلهٔ هدف از تمرین‌های اخیر بالا بود؛ یک ست کم شد.")))))
        applied_deload_ids.update(deload_ids_used_today)
        items, estimated_minutes, day_duration_warnings = _fit_session_duration(
            items,
            maximum_minutes=profile.session_duration_minutes if profile else None,
            exercises_by_id=exercise_by_id,
        )
        duration_warnings.extend(day_duration_warnings)
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
    applied_progression_ids.update(applied_deload_ids)
    return StrengthPlan(days, exercise_by_id, substitution_count, applied_progression_ids, duration_warnings)
