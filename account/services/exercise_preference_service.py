from account.models import AthleteExercisePreference, Exercise


class ExercisePreferenceService:
    @staticmethod
    def record_program_edits(*, athlete, actor, days_payload):
        if not isinstance(days_payload, list):
            return 0
        saved = 0
        source = (
            AthleteExercisePreference.Source.ADMIN
            if getattr(actor, "is_admin", False)
            else AthleteExercisePreference.Source.COACH_EDIT
        )
        for day in days_payload:
            if not isinstance(day, dict):
                continue
            for item in day.get("items") or []:
                if not isinstance(item, dict):
                    continue
                try:
                    generated_id = int(item.get("generated_exercise") or 0)
                    selected_id = int(item.get("exercise") or 0)
                except (TypeError, ValueError):
                    continue
                if not generated_id or not selected_id or generated_id == selected_id:
                    continue
                generated = Exercise.objects.filter(pk=generated_id).first()
                selected = Exercise.objects.filter(pk=selected_id).first()
                if not generated or not selected:
                    continue
                preference, created = AthleteExercisePreference.objects.get_or_create(
                    user=athlete,
                    exercise=generated,
                    defaults={
                        "status": AthleteExercisePreference.Status.AVOID,
                        "source": source,
                        "created_by": actor,
                        "reason": f"در برنامه به حرکت «{selected.name}» جایگزین شد.",
                    },
                )
                if created:
                    saved += 1
                elif preference.status == AthleteExercisePreference.Status.AVOID:
                    preference.source = source
                    preference.created_by = actor
                    preference.reason = f"در برنامه به حرکت «{selected.name}» جایگزین شد."
                    preference.save(update_fields=["source", "created_by", "reason", "updated_at"])
        return saved
