from django.db import migrations, models


def mark_existing_coaches(apps, schema_editor):
    User = apps.get_model("account", "User")
    database = schema_editor.connection.alias
    User.objects.using(database).filter(athletes__isnull=False).distinct().update(is_coach=True)


class Migration(migrations.Migration):
    dependencies = [("account", "0080_seed_cardio_goal_prescriptions")]

    operations = [
        migrations.AddField(
            model_name="user",
            name="is_coach",
            field=models.BooleanField(db_index=True, default=False, verbose_name="مربی"),
        ),
        migrations.RunPython(mark_existing_coaches, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="user",
            name="coach",
            field=models.ForeignKey(
                blank=True,
                limit_choices_to=models.Q(("is_admin", True), ("is_coach", True), _connector="OR"),
                null=True,
                on_delete=models.SET_NULL,
                related_name="athletes",
                to="account.user",
                verbose_name="مربی",
            ),
        ),
    ]
