from django.db import migrations


GOAL_ALIASES = {
    "general": "general",
    "ggeneral": "general",
    "general fitness": "general",
    "mobility": "general",
    "strength": "strength",
    "hypertrophy": "hypertrophy",
    "volume": "hypertrophy",
    "muscle gain": "hypertrophy",
    "fat burning": "fat_burning",
    "fat burn": "fat_burning",
    "body composition": "fat_burning",
    "endurance": "endurance",
    "power": "power",
    "sport performance": "power",
    "cardio": "cardio",
    "aerobic": "cardio",
    "عمومی": "general",
    "تناسب عمومی": "general",
    "سلامت و آمادگی عمومی": "general",
    "انعطاف و تحرک": "general",
    "قدرت": "strength",
    "افزایش قدرت": "strength",
    "حجم": "hypertrophy",
    "افزایش حجم عضلانی": "hypertrophy",
    "چربی سوزی": "fat_burning",
    "چربی‌سوزی": "fat_burning",
    "ترکیب بدنی": "fat_burning",
    "استقامت": "endurance",
    "توان": "power",
    "توان انفجاری": "power",
    "عملکرد ورزشی": "power",
    "هوازی": "cardio",
}


def normalize_workout_goals(apps, schema_editor):
    WorkoutProgramExercise = apps.get_model("account", "WorkoutProgramExercise")
    database = schema_editor.connection.alias
    for value in WorkoutProgramExercise.objects.using(database).values_list("goal", flat=True).distinct():
        raw = str(value or "").strip()
        if not raw:
            continue
        normalized = " ".join(raw.casefold().replace("_", " ").replace("-", " ").split())
        canonical = GOAL_ALIASES.get(normalized, normalized.replace(" ", "_"))
        if canonical != value:
            WorkoutProgramExercise.objects.using(database).filter(goal=value).update(goal=canonical)


class Migration(migrations.Migration):
    dependencies = [("account", "0106_alter_athletetrainingprofile_primary_goal")]

    operations = [
        migrations.RunPython(normalize_workout_goals, migrations.RunPython.noop),
    ]
