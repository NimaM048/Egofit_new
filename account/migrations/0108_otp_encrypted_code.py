from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("account", "0107_normalize_workout_program_goals"),
    ]

    operations = [
        migrations.AddField(
            model_name="otp",
            name="code_encrypted",
            field=models.CharField(blank=True, default="", max_length=255, verbose_name="کد رمزگذاری‌شده"),
        ),
    ]
