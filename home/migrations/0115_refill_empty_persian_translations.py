"""
Fill empty Persian (default-language) translation columns from the base column.

0112 copied base -> *_fa once, but rows that were imported into a database
after 0112 had run (e.g. during the SQLite -> MySQL consolidation) kept only
the base column, so their titles/bodies rendered blank in Persian. This
migration only touches *_fa values that are NULL or empty, so it never
overwrites text that was edited afterwards, and it is safe to re-run.
"""
from django.db import migrations
from django.db.models import Q

FIELDS = {
    "ArticleBlogModel": (
        "title", "article_excerpt", "article_description", "built_in",
        "article_extra_des1", "article_extra_des2", "author_description",
    ),
    "SeriesModel": (
        "title", "description", "introdution_course", "course_extra_des1",
        "course_extra_des2", "built_in", "author_description",
    ),
}


def refill_empty_persian(apps, schema_editor):
    for model_name, fields in FIELDS.items():
        model = apps.get_model("home", model_name)
        for field in fields:
            fa = f"{field}_fa"
            empty_fa = Q(**{f"{fa}__isnull": True}) | Q(**{fa: ""})
            filled_base = ~Q(**{f"{field}__isnull": True}) & ~Q(**{field: ""})
            for pk, value in model.objects.filter(empty_fa & filled_base).values_list("pk", field):
                model.objects.filter(pk=pk).update(**{fa: value})


class Migration(migrations.Migration):

    dependencies = [
        ("home", "0114_articleblogmodel_publication_status"),
    ]

    operations = [
        migrations.RunPython(refill_empty_persian, migrations.RunPython.noop),
    ]
