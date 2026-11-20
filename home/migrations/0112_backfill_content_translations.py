from django.db import migrations
from django.db.models import F
from django.utils.text import slugify


ARTICLE_FIELDS = (
    "title",
    "article_excerpt",
    "article_description",
    "built_in",
    "article_extra_des1",
    "article_extra_des2",
    "author_description",
)

SERIES_FIELDS = (
    "title",
    "description",
    "introdution_course",
    "course_extra_des1",
    "course_extra_des2",
    "built_in",
    "author_description",
)


def copy_persian_values(apps, schema_editor):
    ArticleBlogModel = apps.get_model("home", "ArticleBlogModel")
    SeriesModel = apps.get_model("home", "SeriesModel")

    article_updates = {f"{field}_fa": F(field) for field in ARTICLE_FIELDS}
    article_updates["title_fa"] = F("title")
    ArticleBlogModel.objects.all().update(**article_updates)

    series_updates = {f"{field}_fa": F(field) for field in SERIES_FIELDS}
    series_updates["title_fa"] = F("title")
    SeriesModel.objects.all().update(**series_updates)

    CategoryBlog = apps.get_model("home", "CategoryBlog")
    for category in CategoryBlog.objects.exclude(title_en__isnull=True).exclude(title_en=""):
        derived_slug = slugify(category.title_en)
        if derived_slug:
            CategoryBlog.objects.filter(pk=category.pk).update(slug=derived_slug)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("home", "0111_blogsubcategory_and_more"),
    ]

    operations = [
        migrations.RunPython(copy_persian_values, noop),
    ]
