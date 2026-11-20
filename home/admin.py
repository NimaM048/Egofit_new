from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from django import forms
from django.contrib import admin
from django.core.files.storage import default_storage
from django.http import JsonResponse
from django.urls import path, reverse
from django.utils.html import format_html
from modeltranslation.admin import TranslationAdmin, TranslationTabularInline

from home.models import (
    AboutPage,
    Activity,
    Advantage,
    ArticleBlogImage,
    ArticleBlogLink,
    ArticleBlogModel,
    BlogSubCategory,
    BlogListingPage,
    Category,
    CategoryBlog,
    Comment,
    CommentSectionModel,
    Community,
    ContactPage,
    ContactPageSignal,
    Counseling,
    DownContent,
    Episode,
    Footer,
    HomePage,
    HomePageFeaturePoint,
    HomePageHighlightCard,
    HomePageMetric,
    HomePageTrustItem,
    NavigationLink,
    Reply,
    Season,
    SeriesListingPage,
    SeriesListingSignal,
    SeriesModel,
    SiteSettings,
)
from home.image_utils import INLINE_IMAGE_MAX_DIMENSION, apply_author_image, compress_image_file
from home.rich_text import sanitize_rich_text
from home.services.content_service import invalidate_blog_caches


def _image_preview(file_field, *, height=60):
    if not file_field:
        return "—"
    return format_html('<img src="{}" style="height:{}px;border-radius:8px;" />', file_field.url, height)


class RichTextWidget(forms.Textarea):
    class Media:
        css = {"all": ("css/article-editor.css",)}
        js = ("js/article-editor.js",)

    def __init__(self, attrs=None):
        default_attrs = {"class": "rich-text-source", "rows": 12}
        if attrs:
            default_attrs.update(attrs)
        super().__init__(attrs=default_attrs)


RICH_TEXT_FIELDS = ("article_description", "built_in", "article_extra_des1", "article_extra_des2")


def _translated_field_names(base_name: str) -> tuple[str, ...]:
    return (base_name, f"{base_name}_fa", f"{base_name}_en")


class ArticleBlogAdminForm(forms.ModelForm):
    class Meta:
        model = ArticleBlogModel
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        try:
            upload_url = reverse("admin:home_articleblogmodel_upload_inline_image")
        except Exception:
            upload_url = ""
        for base_name in RICH_TEXT_FIELDS:
            for field_name in _translated_field_names(base_name):
                field = self.fields.get(field_name)
                if field is None:
                    continue
                field.widget = RichTextWidget(
                    attrs={
                        "class": "rich-text-source",
                        "rows": 12,
                        "data-image-upload-url": upload_url,
                    }
                )

    def clean(self):
        cleaned_data = super().clean()
        for base_name in RICH_TEXT_FIELDS:
            for field_name in _translated_field_names(base_name):
                if field_name in cleaned_data:
                    cleaned_data[field_name] = sanitize_rich_text(cleaned_data.get(field_name))
        return cleaned_data


class ArticleBlogImageInline(admin.TabularInline):
    model = ArticleBlogImage
    extra = 1
    fields = ("image", "alt_text", "caption", "sort_order")
    ordering = ("sort_order", "id")


class ArticleBlogLinkInline(admin.TabularInline):
    model = ArticleBlogLink
    extra = 1
    fields = ("label", "url", "open_in_new_tab", "sort_order")
    ordering = ("sort_order", "id")


@admin.register(Episode)
class EpisodeAdmin(admin.ModelAdmin):
    list_display = ("title", "episode_number", "season", "download_uploaded_at")
    search_fields = ("title",)
    list_filter = ("season",)
    fieldsets = (
        (None, {"fields": ("season", "episode_number", "title", "duration", "video_file")}),
        (
            "فایل دانلودی (اختیاری)",
            {
                "fields": ("download_file", "download_uploaded_at"),
                "description": "در صورت نیاز می‌توانید یک فایل برای دانلود کاربران روی این اپیزود قرار دهید.",
            },
        ),
    )
    readonly_fields = ("download_uploaded_at",)


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "email", "phone", "publication_status")
    list_select_related = ("user",)
    search_fields = (
        "name",
        "email",
        "phone",
        "user__fullname",
        "user__display_name",
        "user__phone",
        "user__email",
    )
    search_help_text = "جستجو بر اساس نام نظر‌دهنده، کاربر، شماره تماس یا ایمیل"
    list_filter = ("publication_status",)
    autocomplete_fields = ("user",)


@admin.register(SeriesModel)
class SeriesModelAdmin(TranslationAdmin):
    list_display = ("title", "author", "is_compeleted", "free")
    search_fields = ("title", "author__fullname", "author__phone")
    list_filter = ("is_compeleted", "free", "language_kinds")
    autocomplete_fields = ("author", "language_kinds")
    fieldsets = (
        (
            "اطلاعات اصلی",
            {
                "fields": (
                    "title",
                    "image",
                    "author",
                    "language_kinds",
                    "is_published",
                    "is_compeleted",
                    "free",
                    "main_price",
                    "discount_price",
                    "seseaons",
                    "hours",
                ),
            },
        ),
        (
            "توضیحات دوره",
            {
                "fields": (
                    "description",
                    "introdution_course",
                    "course_extra_des1",
                    "course_extra_des2",
                    "built_in",
                ),
                "classes": ("wide",),
            },
        ),
        (
            "نویسنده",
            {
                "fields": ("author_image", "author_description"),
            },
        ),
    )

    def save_model(self, request, obj, form, change):
        if not change:
            apply_author_image(obj)
        super().save_model(request, obj, form, change)

    @admin.action(description="تایید و انتشار مقالات انتخاب‌شده")
    def approve_articles(self, request, queryset):
        updated = queryset.update(publication_status=ArticleBlogModel.PublicationStatus.APPROVED)
        self.message_user(request, f"{updated} مقاله تایید و منتشر شد.")
        invalidate_blog_caches()

    @admin.action(description="رد مقالات انتخاب‌شده")
    def reject_articles(self, request, queryset):
        updated = queryset.update(publication_status=ArticleBlogModel.PublicationStatus.REJECTED)
        self.message_user(request, f"{updated} مقاله رد شد.")
        invalidate_blog_caches()


@admin.register(ArticleBlogModel)
class ArticleBlogModelAdmin(TranslationAdmin):
    form = ArticleBlogAdminForm
    list_display = ("title", "author", "reading_time", "publication_status")
    search_fields = ("title", "author__fullname", "author__phone", "slug")
    list_filter = ("publication_status", "reading_time", "language_kinds", "subcategory")
    autocomplete_fields = ("author", "language_kinds", "subcategory")
    inlines = (ArticleBlogImageInline, ArticleBlogLinkInline)
    actions = ("approve_articles", "reject_articles")
    fieldsets = (
        (
            "اطلاعات اصلی",
            {
                "fields": (
                    "title",
                    "slug",
                    "language_kinds",
                    "subcategory",
                    "image",
                    "author",
                    "reading_time",
                    "publication_status",
                    "article_excerpt",
                    "author_image",
                    "author_description",
                ),
            },
        ),
        (
            "محتوای مقاله",
            {
                "description": "برای قالب‌بندی متن از نوار ابزار استفاده کنید. لینک‌های قابل مشاهده مقاله را از بخش «لینک‌های مقاله» پایین فرم اضافه کنید.",
                "fields": (
                    "article_description",
                    "built_in",
                    "article_extra_des1",
                    "article_extra_des2",
                ),
                "classes": ("wide",),
            },
        ),
    )

    def get_urls(self):
        custom_urls = [
            path(
                "upload-inline-image/",
                self.admin_site.admin_view(self.upload_inline_image),
                name="home_articleblogmodel_upload_inline_image",
            ),
        ]
        return custom_urls + super().get_urls()

    def upload_inline_image(self, request):
        if request.method != "POST":
            return JsonResponse({"error": "روش درخواست پشتیبانی نمی‌شود."}, status=405)
        if not self.has_add_permission(request) and not self.has_change_permission(request):
            return JsonResponse({"error": "دسترسی کافی وجود ندارد."}, status=403)

        upload = request.FILES.get("file")
        if not upload:
            return JsonResponse({"error": "فایلی انتخاب نشده است."}, status=400)
        if not (getattr(upload, "content_type", "") or "").startswith("image/"):
            return JsonResponse({"error": "فقط فایل تصویری مجاز است."}, status=400)

        compressed = compress_image_file(
            upload,
            max_dimension=INLINE_IMAGE_MAX_DIMENSION,
        )
        suffix = Path(upload.name).suffix.lower() or ".jpg"
        stored_name = default_storage.save(f"articles_blog/inline/{uuid4().hex}{suffix}", compressed)
        return JsonResponse({"url": default_storage.url(stored_name)})

    def save_model(self, request, obj, form, change):
        if not change:
            apply_author_image(obj)
        super().save_model(request, obj, form, change)


@admin.register(Season)
class SeasonAdmin(admin.ModelAdmin):
    list_display = ("title", "series", "number")
    search_fields = ("title", "series__title")
    list_filter = ("series",)


@admin.register(Category)
class CategoryAdmin(TranslationAdmin):
    list_display = ("title", "slug")
    prepopulated_fields = {"slug": ("title",)}
    search_fields = ("title", "slug")


@admin.register(Footer)
class FooterAdmin(TranslationAdmin):
    list_display = ("about_us", "phone_number", "email", "working_hours", "address")
    fieldsets = (
        (
            "About Us",
            {
                "fields": ("about_eyebrow", "about_title", "about_us"),
                "classes": ("wide",),
            },
        ),
        (
            "Contact Information",
            {
                "fields": ("phone_number", "email", "address", "working_hours"),
                "classes": ("wide",),
            },
        ),
        (
            "Links & Social",
            {
                "fields": ("links_section_title", "social_section_title", "show_social_media", "social_media"),
                "classes": ("wide",),
            },
        ),
        (
            "Trust badge & Copyright",
            {
                "fields": ("trust_text", "copyright_year"),
                "classes": ("wide",),
            },
        ),
    )

    def has_add_permission(self, request):
        return not Footer.objects.exists()


class HomePageMetricInline(TranslationTabularInline):
    model = HomePageMetric
    extra = 0
    min_num = 0


class HomePageTrustItemInline(TranslationTabularInline):
    model = HomePageTrustItem
    extra = 0
    min_num = 0


class HomePageFeaturePointInline(TranslationTabularInline):
    model = HomePageFeaturePoint
    extra = 0
    min_num = 0


class HomePageHighlightCardInline(TranslationTabularInline):
    model = HomePageHighlightCard
    extra = 0
    min_num = 0


@admin.register(HomePage)
class HomePageAdmin(TranslationAdmin):
    list_display = ("title", "slug")
    readonly_fields = ("slug",)
    inlines = [
        HomePageMetricInline,
        HomePageTrustItemInline,
        HomePageFeaturePointInline,
        HomePageHighlightCardInline,
    ]
    fieldsets = (
        (
            None,
            {
                "fields": ("title", "slug"),
            },
        ),
        (
            "هدر اصلی",
            {
                "fields": (
                    "hero_eyebrow",
                    "hero_title",
                    "hero_description",
                    "hero_primary_label",
                    "hero_primary_url",
                    "hero_secondary_label",
                    "hero_secondary_url",
                    "hero_image",
                    "hero_image_alt",
                )
            },
        ),
        (
            "بخش آمار",
            {
                "fields": (
                    "stats_eyebrow",
                    "stats_title",
                    "stats_description",
                    "stat_total_courses_label",
                    "stat_free_courses_label",
                    "stat_completed_courses_label",
                    "stat_articles_label",
                )
            },
        ),
        (
            "بخش دوره‌ها",
            {
                "fields": (
                    "featured_courses_eyebrow",
                    "featured_courses_title",
                    "featured_courses_description",
                    "featured_courses_button_label",
                    "featured_courses_button_url",
                )
            },
        ),
        (
            "بخش وبلاگ",
            {
                "fields": (
                    "article_eyebrow",
                    "article_title",
                    "article_description",
                    "article_button_label",
                    "article_button_url",
                )
            },
        ),
    )

    def has_add_permission(self, request):
        return not HomePage.objects.exists()


class ReplyInline(admin.TabularInline):
    model = Reply
    extra = 1


@admin.register(CommentSectionModel)
class CommentSectionModelAdmin(admin.ModelAdmin):
    list_display = ("__str__", "user", "created_at", "publication_status")
    inlines = [ReplyInline]
    autocomplete_fields = ("user", "series", "parent")
    search_fields = ("text", "user__fullname", "series__title")
    list_filter = ("created_at", "publication_status", "user")


@admin.register(Reply)
class ReplyAdmin(admin.ModelAdmin):
    list_display = ("__str__", "created_at")
    search_fields = ("text", "user__fullname", "user__phone", "comment__text")
    search_help_text = "جستجو بر اساس متن پاسخ، نام کاربر، شماره تماس یا متن کامنت"
    list_filter = ("created_at", "is_active")
    autocomplete_fields = ("user", "comment")


@admin.register(Counseling)
class CounselingAdmin(TranslationAdmin):
    list_display = ("title", "subtitle")
    search_fields = ("title", "subtitle")


@admin.register(AboutPage)
class AboutPageAdmin(TranslationAdmin):
    list_display = ("title", "description")
    search_fields = ("title", "description")


@admin.register(Activity)
class ActivityAdmin(TranslationAdmin):
    list_display = ("title", "description")
    search_fields = ("title", "description")


@admin.register(Community)
class CommunityAdmin(TranslationAdmin):
    list_display = ("title", "description")
    search_fields = ("title", "description")


admin.site.register(Advantage, TranslationAdmin)
admin.site.register(DownContent, TranslationAdmin)


class BlogSubCategoryInline(TranslationTabularInline):
    model = BlogSubCategory
    extra = 1
    fields = ("title", "slug", "description", "sort_order")
    ordering = ("sort_order", "id")


@admin.register(CategoryBlog)
class CategoryBlogAdmin(TranslationAdmin):
    list_display = ("title", "slug", "subcategory_count")
    search_fields = ("title", "slug")
    readonly_fields = ("slug",)
    inlines = (BlogSubCategoryInline,)

    @admin.display(description="تعداد زیربخش")
    def subcategory_count(self, obj):
        return obj.subcategories.count()


@admin.register(BlogSubCategory)
class BlogSubCategoryAdmin(TranslationAdmin):
    list_display = ("title", "category", "slug", "sort_order")
    list_filter = ("category",)
    search_fields = ("title", "slug", "category__title")
    autocomplete_fields = ("category",)
    readonly_fields = ("slug",)
    ordering = ("category", "sort_order", "id")


@admin.register(SiteSettings)
class SiteSettingsAdmin(TranslationAdmin):
    list_display = ("brand_name", "tagline", "logo_preview")
    readonly_fields = ("slug", "logo_preview")
    fieldsets = (
        (
            None,
            {
                "fields": ("brand_eyebrow", "brand_name", "logo", "logo_preview", "tagline", "header_search_placeholder", "default_meta_title"),
            },
        ),
    )

    @admin.display(description="پیش‌نمایش لوگو")
    def logo_preview(self, obj):
        return _image_preview(obj.logo)

    def has_add_permission(self, request):
        return not SiteSettings.objects.exists()


@admin.register(NavigationLink)
class NavigationLinkAdmin(TranslationAdmin):
    list_display = ("label", "url", "sort_order")
    list_editable = ("sort_order",)
    ordering = ("sort_order", "id")


class ContactPageSignalInline(TranslationTabularInline):
    model = ContactPageSignal
    extra = 0
    min_num = 0


@admin.register(ContactPage)
class ContactPageAdmin(TranslationAdmin):
    readonly_fields = ("slug",)
    inlines = [ContactPageSignalInline]
    fieldsets = (
        (
            "هدر",
            {
                "fields": ("slug", "hero_eyebrow", "hero_title", "hero_description"),
            },
        ),
        (
            "کادر وضعیت",
            {
                "fields": ("status_title", "status_description"),
            },
        ),
    )

    def has_add_permission(self, request):
        return not ContactPage.objects.exists()


class SeriesListingSignalInline(TranslationTabularInline):
    model = SeriesListingSignal
    extra = 0
    min_num = 0


@admin.register(SeriesListingPage)
class SeriesListingPageAdmin(TranslationAdmin):
    readonly_fields = ("slug",)
    inlines = [SeriesListingSignalInline]
    fieldsets = (
        (
            "هدر",
            {
                "fields": ("slug", "hero_eyebrow", "hero_title", "hero_description"),
            },
        ),
    )

    def has_add_permission(self, request):
        return not SeriesListingPage.objects.exists()


@admin.register(BlogListingPage)
class BlogListingPageAdmin(TranslationAdmin):
    readonly_fields = ("slug",)
    fieldsets = (
        (
            "هدر",
            {
                "fields": ("slug", "hero_eyebrow", "hero_title", "hero_description"),
            },
        ),
    )

    def has_add_permission(self, request):
        return not BlogListingPage.objects.exists()
