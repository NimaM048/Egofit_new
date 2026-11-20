from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("home", "0109_comment_publication_status_and_more")]

    operations = [
        migrations.AddField(
            model_name="seriesmodel",
            name="is_published",
            field=models.BooleanField(db_index=True, default=True, verbose_name="منتشر شده"),
        ),
    ]
