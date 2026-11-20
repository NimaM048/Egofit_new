"""Read and normalize the shared exercise and athlete goal catalog."""

from __future__ import annotations

from account.domain.exercise_goals import canonical_goal_key
from account.models import ExerciseGoal


class ExerciseGoalService:
    @classmethod
    def canonical_key(cls, value: object) -> str:
        """Resolve a catalog key or localized label to its shared key."""
        raw = str(value or "").strip()
        if not raw:
            return ""
        goal = ExerciseGoal.objects.filter(name_en__iexact=raw).order_by("pk").first()
        if goal is None:
            goal = ExerciseGoal.objects.filter(name__iexact=raw).order_by("pk").first()
        return canonical_goal_key(goal.name_en if goal else raw)

    @classmethod
    def choices(cls) -> tuple[tuple[str, str], ...]:
        choices = []
        seen = set()
        for goal in ExerciseGoal.objects.order_by("pk"):
            key = canonical_goal_key(goal.name_en)
            if key and key not in seen:
                choices.append((key, goal.name))
                seen.add(key)
        return tuple(choices)

    @classmethod
    def catalog(cls) -> list[dict[str, object]]:
        items = []
        seen = set()
        for goal in ExerciseGoal.objects.order_by("pk"):
            key = canonical_goal_key(goal.name_en)
            if not key or key in seen:
                continue
            items.append({"id": goal.pk, "key": key, "name": goal.name})
            seen.add(key)
        return items

    @classmethod
    def label(cls, value: object) -> str:
        key = cls.canonical_key(value)
        for goal in ExerciseGoal.objects.order_by("pk"):
            if canonical_goal_key(goal.name_en) == key:
                return goal.name
        return key or str(value or "")
