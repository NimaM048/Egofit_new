"""Staff course management and coach article workflows."""

from __future__ import annotations

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404

from account.validators import validate_image_upload, validate_video_upload
from api.decorators import api_auth_required, api_endpoint, api_methods
from api.limits import rate_limit
from api.responses import ok
from api.utils import absolute_file_url, parse_int, request_data
from api.v1.permissions import api_admin_required, api_coach_required
from home.models import ArticleBlogModel, Category, Episode, Season, SeriesModel
from home.services.content_service import invalidate_blog_caches
from home.constants import HOME_PAGE_CACHE_PREFIX
from home.selectors.course_selector import SERIES_COUNT_CACHE_KEY

from .support import _audit, _page, _multipart_bool


@api_endpoint
@api_methods("GET", "POST")
@api_auth_required
@api_admin_required
def admin_courses(request):
    if request.method == "GET":
        from api.v1.common import _serialize_series
        queryset = SeriesModel.objects.select_related("author", "language_kinds").order_by("-pk")
        return ok(_page(request, queryset, lambda series: {**_serialize_series(request, series), "is_published": series.is_published}))

    allowed_fields = {"title", "free", "category_id", "is_completed", "description", "introduction", "main_price", "discount_price"}
    if set(request.POST) - allowed_fields or set(request.FILES) - {"image"}:
        raise ValidationError({"fields": "Unsupported course upload fields."})
    title = (request.POST.get("title") or "").strip()
    if not title or len(title) > 50:
        raise ValidationError({"title": "title is required and must be at most 50 characters."})
    image = request.FILES.get("image")
    if not image:
        raise ValidationError({"image": "Course image is required."})
    validate_image_upload(image)
    free = _multipart_bool(request.POST.get("free"), field="free", default=True)
    category_id = request.POST.get("category_id")
    category = get_object_or_404(Category, pk=parse_int(category_id, field="category_id")) if category_id else None
    series = SeriesModel(
        title=title,
        image=image,
        author=request.user,
        language_kinds=category,
        free=free,
        is_published=True,
        is_compeleted=_multipart_bool(request.POST.get("is_completed"), field="is_completed"),
        description=(request.POST.get("description") or "").strip(),
        introdution_course=(request.POST.get("introduction") or "").strip(),
        main_price=(request.POST.get("main_price") or "").strip()[:20],
        discount_price=(request.POST.get("discount_price") or "").strip()[:20],
    )
    series.full_clean()
    series.save()
    cache.delete(f"{HOME_PAGE_CACHE_PREFIX}:home:v1")
    cache.delete(SERIES_COUNT_CACHE_KEY)
    _audit(request.user, "course.create", object_type="course", object_id=series.pk)
    from api.v1.common import _serialize_series
    return ok({**_serialize_series(request, series), "is_published": series.is_published}, status=201)


@api_endpoint
@api_methods("GET", "PATCH")
@api_auth_required
@api_admin_required
def admin_course_detail(request, course_id):
    if request.method == "GET":
        from api.v1.common import _serialize_series

        course = get_object_or_404(
            SeriesModel.objects.select_related("author", "language_kinds")
            .prefetch_related("seasons__episodes"),
            pk=course_id,
        )
        payload = {**_serialize_series(request, course), "is_published": course.is_published}
        payload["seasons"] = [
            {
                "id": season.pk,
                "number": season.number,
                "title": season.title,
                "episodes": [
                    {
                        "id": episode.pk,
                        "episode_number": episode.episode_number,
                        "title": episode.title,
                        "duration": episode.duration,
                    }
                    for episode in season.episodes.all()
                ],
            }
            for season in course.seasons.all()
        ]
        return ok(payload)
    data = request_data(request)
    if set(data) != {"is_published"} or not isinstance(data.get("is_published"), bool):
        raise ValidationError({"is_published": "A boolean is_published field is required."})
    course = get_object_or_404(SeriesModel, pk=course_id)
    course.is_published = data["is_published"]
    course.save(update_fields=["is_published"])
    cache.delete(f"{HOME_PAGE_CACHE_PREFIX}:home:v1")
    cache.delete(SERIES_COUNT_CACHE_KEY)
    _audit(request.user, "course.publication.update", object_type="course", object_id=course.pk)
    return ok({"id": course.pk, "is_published": course.is_published})


@api_endpoint
@api_methods("POST")
@api_auth_required
@api_admin_required
def admin_course_seasons(request, course_id):
    data = request_data(request)
    if set(data) - {"number", "title"} or not str(data.get("title") or "").strip():
        raise ValidationError({"season": "title is required; only number and title are accepted."})
    course = get_object_or_404(SeriesModel, pk=course_id)
    number = parse_int(data.get("number", course.seasons.count() + 1), field="number")
    if number < 1:
        raise ValidationError({"number": "number must be greater than zero."})
    season = Season.objects.create(series=course, number=number, title=str(data["title"]).strip()[:50])
    _audit(request.user, "course.season.create", object_type="season", object_id=season.pk)
    return ok({"id": season.pk, "series_id": course.pk, "number": season.number, "title": season.title}, status=201)


@api_endpoint
@api_methods("POST")
@api_auth_required
@api_admin_required
@rate_limit(name="admin-course-video-upload", limit=20, window=3600)
def admin_season_episodes(request, season_id):
    season = get_object_or_404(Season.objects.select_related("series"), pk=season_id)
    if set(request.POST) - {"title", "episode_number"} or set(request.FILES) - {"video_file"}:
        raise ValidationError({"fields": "Unsupported episode upload fields."})
    title = (request.POST.get("title") or "").strip()
    video = request.FILES.get("video_file")
    if not title or len(title) > 255:
        raise ValidationError({"title": "title is required and must be at most 255 characters."})
    if video:
        validate_video_upload(video)
    number = parse_int(request.POST.get("episode_number", season.episodes.count() + 1), field="episode_number")
    if number < 1:
        raise ValidationError({"episode_number": "episode_number must be greater than zero."})
    episode = Episode.objects.create(season=season, title=title, episode_number=number, video_file=video)
    _audit(request.user, "course.episode.create", object_type="episode", object_id=episode.pk)
    return ok({"id": episode.pk, "season_id": season.pk, "title": episode.title, "episode_number": episode.episode_number, "duration": episode.duration}, status=201)


def _serialize_blog_article(request, article, *, detail=False):
    payload = {
        "id": article.pk,
        "slug": article.slug,
        "title": article.title,
        "reading_time": article.reading_time,
        "excerpt": article.article_excerpt,
        "publication_status": article.publication_status,
        "publication_status_label": article.get_publication_status_display(),
        "image": absolute_file_url(request, article.image) if article.image else None,
        "author": {"id": article.author_id, "fullname": article.author.fullname} if article.author_id else None,
    }
    if detail:
        payload["description"] = article.article_description
    return payload


def _validate_blog_submission(request):
    """Extract and validate the fields a coach may submit for a new post."""
    allowed = {"title", "reading_time", "article_excerpt", "article_description", "built_in", "subcategory"}
    if set(request.POST) - allowed or set(request.FILES) - {"image"}:
        raise ValidationError({"fields": "Unsupported article fields."})
    title = (request.POST.get("title") or "").strip()
    if not title or len(title) > 100:
        raise ValidationError({"title": "title is required and must be at most 100 characters."})
    image = request.FILES.get("image")
    if not image:
        raise ValidationError({"image": "An article image is required."})
    validate_image_upload(image)
    try:
        reading_time = parse_int(request.POST.get("reading_time", 5), field="reading_time")
    except ValueError:
        reading_time = 5
    if reading_time < 1 or reading_time > 240:
        raise ValidationError({"reading_time": "reading_time must be between 1 and 240 minutes."})
    return {
        "title": title,
        "reading_time": reading_time,
        "article_excerpt": (request.POST.get("article_excerpt") or "").strip(),
        "article_description": (request.POST.get("article_description") or "").strip(),
        "built_in": (request.POST.get("built_in") or "").strip(),
        "subcategory": request.POST.get("subcategory") or None,
    }


@api_endpoint
@api_methods("GET", "POST")
@api_auth_required
@api_coach_required
@rate_limit(name="coach-blog-submit", limit=10, window=3600)
def coach_articles(request):
    """A coach's own posts. New submissions stay pending until an admin approves."""
    if request.method == "GET":
        queryset = (
            ArticleBlogModel.objects.filter(author=request.user)
            .select_related("author")
            .order_by("-id")[:50]
        )
        return ok({"items": [_serialize_blog_article(request, article) for article in queryset]})

    from home.models import BlogSubCategory

    fields = _validate_blog_submission(request)
    subcategory = None
    if fields["subcategory"]:
        # The coach may only attach the post to an existing subcategory, and may
        # not smuggle another author or status through the payload.
        subcategory = get_object_or_404(BlogSubCategory, pk=parse_int(fields["subcategory"], field="subcategory"))
    article = ArticleBlogModel(
        title=fields["title"],
        image=request.FILES["image"],
        author=request.user,
        reading_time=fields["reading_time"],
        article_excerpt=fields["article_excerpt"],
        article_description=fields["article_description"],
        built_in=fields["built_in"],
        subcategory=subcategory,
        publication_status=ArticleBlogModel.PublicationStatus.PENDING,
    )
    article.full_clean()
    article.save()
    invalidate_blog_caches()
    _audit(request.user, "coach.article.submit", object_type="article", object_id=article.pk)
    return ok({"article": _serialize_blog_article(request, article)}, status=201)


@api_endpoint
@api_methods("GET")
@api_auth_required
@api_admin_required
def admin_articles(request):
    """Moderation queue of all articles, filterable by publication status."""
    queryset = ArticleBlogModel.objects.select_related("author").order_by("-id")
    status = (request.GET.get("status") or "").strip()
    if status in {choice for choice, _label in ArticleBlogModel.PublicationStatus.choices}:
        queryset = queryset.filter(publication_status=status)
    return ok(_page(request, queryset, lambda article: _serialize_blog_article(request, article, detail=True)))


@api_endpoint
@api_methods("PATCH")
@api_auth_required
@api_admin_required
def admin_article_moderate(request, article_id):
    """Approve or reject a coach-authored post so it can appear on the site."""
    data = request_data(request)
    valid_statuses = {choice for choice, _label in ArticleBlogModel.PublicationStatus.choices}
    if set(data) != {"publication_status"} or data.get("publication_status") not in valid_statuses:
        raise ValidationError({"publication_status": "A valid publication_status is required."})
    with transaction.atomic():
        article = get_object_or_404(ArticleBlogModel.objects.select_for_update(), pk=article_id)
        article.publication_status = data["publication_status"]
        article.save(update_fields=["publication_status"])
    invalidate_blog_caches()
    _audit(request.user, "admin.article.moderate", object_type="article", object_id=article.pk)
    return ok({"article": _serialize_blog_article(request, article, detail=True)})
