"""Article and comment workflows for admins and coaches."""

from __future__ import annotations

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views import View

from account.admin_forms import AdminCommentModerationForm
from home.models import ArticleBlogModel, Comment, CommentSectionModel, Reply
from home.forms import CoachBlogPostForm
from home.services.content_service import invalidate_blog_caches
from account.portal_mixins import AdminRequiredMixin, StaffRequiredMixin, is_coach_only
from account.services.notification_service import NotificationService


class AdminCommentModerationView(AdminRequiredMixin, View):
    def post(self, request):
        kind = request.POST.get("kind")
        object_id = request.POST.get("object_id")
        form = AdminCommentModerationForm(request.POST)
        if not form.is_valid():
            messages.error(request, _("عملیات دیدگاه معتبر نیست."))
            return redirect("register:admin_notifications")

        action = form.cleaned_data["action"]
        response_text = (form.cleaned_data.get("response") or "").strip()
        if kind == "opinion":
            item = get_object_or_404(Comment, pk=object_id)
            if action in {"approve", "reject"}:
                item.is_active = action == "approve"
                item.publication_status = Comment.PublicationStatus.APPROVED if action == "approve" else Comment.PublicationStatus.REJECTED
            if response_text:
                item.admin_response = response_text
                item.responded_at = timezone.now()
            item.save(update_fields=["is_active", "publication_status", "admin_response", "responded_at"])
        elif kind == "comment":
            item = get_object_or_404(CommentSectionModel, pk=object_id)
            if action in {"approve", "reject"}:
                item.is_active = action == "approve"
                item.publication_status = CommentSectionModel.PublicationStatus.APPROVED if action == "approve" else CommentSectionModel.PublicationStatus.REJECTED
                item.save(update_fields=["is_active", "publication_status"])
            if response_text:
                Reply.objects.create(comment=item, user=request.user, text=response_text, is_active=True)
        else:
            messages.error(request, _("نوع دیدگاه معتبر نیست."))
            return redirect("register:admin_notifications")

        messages.success(request, _("عملیات دیدگاه با موفقیت ثبت شد."))
        return redirect("register:admin_notifications")


class CoachBlogPostsView(StaffRequiredMixin, View):
    """Coach-authored blog posts; new submissions wait for admin approval."""

    template_name = "account/coach-blog.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not is_coach_only(request.user):
            return redirect("register:admin_search")
        return super().dispatch(request, *args, **kwargs)

    def _context(self, form=None):
        return {
            "form": form or CoachBlogPostForm(),
            "posts": list(
                ArticleBlogModel.objects.filter(author=self.request.user).order_by("-id")[:50]
            ),
            "active_section": "coach_blog",
            "is_coach_only": True,
            "portal_role": "coach",
        }

    def get(self, request, *args, **kwargs):
        return render(request, self.template_name, self._context())

    def post(self, request, *args, **kwargs):
        form = CoachBlogPostForm(request.POST, request.FILES)
        if form.is_valid():
            article = form.save(commit=False)
            article.author = request.user
            article.publication_status = ArticleBlogModel.PublicationStatus.PENDING
            article.author_description = request.user.biography or ""
            article.save()
            invalidate_blog_caches()
            messages.success(
                request,
                _("پست شما ثبت شد و پس از تایید ادمین در بلاگ سایت منتشر می‌شود."),
            )
            return redirect("register:coach_blog")
        return render(request, self.template_name, self._context(form=form))


class AdminArticleModerationView(AdminRequiredMixin, View):
    """Approve or reject coach-authored blog posts before publication."""

    def post(self, request, pk):
        article = get_object_or_404(ArticleBlogModel, pk=pk)
        action = (request.POST.get("action") or "").strip()
        if action not in {"approve", "reject"}:
            messages.error(request, _("عملیات انتخاب‌شده معتبر نیست."))
            return redirect("register:admin_notifications")
        reason = (request.POST.get("reason") or "").strip()
        article.publication_status = (
            ArticleBlogModel.PublicationStatus.APPROVED
            if action == "approve"
            else ArticleBlogModel.PublicationStatus.REJECTED
        )
        article.save(update_fields=["publication_status"])
        invalidate_blog_caches()
        if article.author_id and not article.author.is_admin:
            if action == "approve":
                message_text = _("پست بلاگ «{title}» شما تایید و در سایت منتشر شد.").format(
                    title=article.title
                )
            else:
                message_text = _("پست بلاگ «{title}» شما رد شد.").format(title=article.title)
                if reason:
                    message_text = f"{message_text} {_('دلیل:')} {reason}"
            NotificationService().notify_athlete_news(
                article.author,
                title=str(_("نتیجه بررسی پست بلاگ")),
                message=str(message_text),
            )
        messages.success(
            request,
            _("پست بلاگ تایید و منتشر شد.")
            if action == "approve"
            else _("پست بلاگ رد شد."),
        )
        return redirect("register:admin_notifications")
