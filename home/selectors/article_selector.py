from __future__ import annotations

from home.constants import HOME_LATEST_BLOG_LIMIT, HOME_RELATED_ARTICLE_LIMIT
from home.models import ArticleBlogModel, CategoryBlog


class ArticleSelector:
    @staticmethod
    def get_latest_blogs(limit: int = HOME_LATEST_BLOG_LIMIT):
        return (
            ArticleBlogModel.objects.filter(publication_status=ArticleBlogModel.PublicationStatus.APPROVED)
            .select_related("author", "language_kinds")
            .order_by("-id")[:limit]
        )

    @staticmethod
    def get_blog_categories():
        return CategoryBlog.objects.order_by("id")

    @staticmethod
    def get_blog_subcategories():
        from home.models import BlogSubCategory

        return BlogSubCategory.objects.select_related("category").order_by("category_id", "sort_order", "id")

    @staticmethod
    def get_blog_queryset(category: str | None = None, subcategory: str | None = None):
        queryset = (
            ArticleBlogModel.objects.filter(publication_status=ArticleBlogModel.PublicationStatus.APPROVED)
            .select_related("author", "language_kinds")
            .order_by("-id")
        )
        if category:
            queryset = queryset.filter(language_kinds__title__icontains=category)
        if subcategory:
            queryset = queryset.filter(subcategory__title__icontains=subcategory)
        return queryset

    @staticmethod
    def get_article_by_slug(slug: str):
        return ArticleBlogModel.objects.filter(
            publication_status=ArticleBlogModel.PublicationStatus.APPROVED
        ).select_related("author", "language_kinds").get(slug=slug)

    @staticmethod
    def get_related_articles(article, limit: int = HOME_RELATED_ARTICLE_LIMIT):
        return (
            ArticleBlogModel.objects.filter(publication_status=ArticleBlogModel.PublicationStatus.APPROVED)
            .select_related("author", "language_kinds")
            .exclude(id=article.id)
            .order_by("-id")[:limit]
        )
