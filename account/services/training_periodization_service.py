from __future__ import annotations

from decimal import Decimal, ROUND_DOWN

from django.utils import timezone

from account.services.gym_program_service import GymProgramService


class TrainingPeriodizationService:
    """Resolve the active week and its conservative deload prescription."""

    @staticmethod
    def current_week(program, *, at=None) -> dict:
        metadata = getattr(program, "generation_metadata", None) or {}
        plan = metadata.get("periodization") if isinstance(metadata, dict) else None
        if not isinstance(plan, dict):
            return {"week": 1, "cycle_week": 1, "phase": "standard", "is_deload": False}
        cycle_weeks = max(1, int(plan.get("cycle_weeks") or 4))
        start_date = getattr(program, "start_date", None)
        today = at or timezone.localdate()
        elapsed_days = max(0, (today - start_date).days) if start_date else 0
        week = (elapsed_days // 7) + 1
        cycle_week = ((week - 1) % cycle_weeks) + 1
        configured_deload_week = plan.get("deload_week")
        deload_week = int(configured_deload_week) if configured_deload_week is not None else cycle_weeks
        is_deload = cycle_weeks > 1 and cycle_week == deload_week
        return {
            "week": week,
            "cycle_week": cycle_week,
            "phase": "deload" if is_deload else "build",
            "is_deload": is_deload,
        }

    @classmethod
    def active_sets(cls, program, sets_value, *, item=None, at=None) -> str:
        current = cls.current_week(program, at=at)
        weekly_targets = getattr(item, "weekly_set_targets", None) if item is not None else None
        if isinstance(weekly_targets, dict):
            target = weekly_targets.get(str(current["cycle_week"]))
            if target:
                return str(target)
        metadata = getattr(program, "generation_metadata", None) or {}
        plan = metadata.get("periodization") if isinstance(metadata, dict) else None
        if not current["is_deload"] or not isinstance(plan, dict):
            return str(sets_value or "")
        factor = Decimal(str(plan.get("deload_sets_multiplier") or "0.7"))
        low, high = GymProgramService._range_from_text(sets_value)
        if low is None or high is None:
            return str(sets_value or "")
        low = max(1, int((Decimal(low) * factor).quantize(Decimal("1"), rounding=ROUND_DOWN)))
        high = max(low, int((Decimal(high) * factor).quantize(Decimal("1"), rounding=ROUND_DOWN)))
        return str(low) if low == high else f"{low}–{high}"
