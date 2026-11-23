"""Staff notifications and coach health alert settings."""

from __future__ import annotations

import jdatetime
from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import TemplateView

from account.models import AthleteHealthAlert, BirthdaySmsLog, MoodReminderSubscription
from home.models import ArticleBlogModel, Comment, CommentSectionModel
from account.portal_mixins import StaffRequiredMixin, is_coach_only

from .mixins import AdminPageMixin
from .services import (
    admin_portal_service,
    coach_application_service,
    exercise_suggestion_service,
    health_alert_service,
)


class AdminNotificationsView(AdminPageMixin, TemplateView):
    template_name = "admin_portal/notifications.html"
    active_section = "notifications"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        admin_portal_service.send_pending_birthday_sms()
        today_jalali = jdatetime.date.today().strftime("%Y/%m/%d")
        sent_user_ids = set(
            BirthdaySmsLog.objects.filter(sent_on=today_jalali).values_list("user_id", flat=True)
        )
        birthday_users = [
            {"user": user, "sms_sent": user.pk in sent_user_ids}
            for user in admin_portal_service.get_birthday_users()
        ]
        context.update(
            {
                "pending_coach_requests": list(admin_portal_service.coach_request_service.get_pending_queryset()),
                "pending_coach_applications": list(
                    coach_application_service.get_pending_queryset().select_related("user")[:10]
                ),
                "pending_exercise_suggestions": list(
                    exercise_suggestion_service.get_pending_queryset()[:10]
                ),
                "birthday_users": birthday_users,
                "pending_opinions": list(
                    Comment.objects.filter(publication_status=Comment.PublicationStatus.PENDING)
                    .select_related("user")
                    .order_by("-id")
                ),
                "pending_comments": list(
                    CommentSectionModel.objects.filter(publication_status=CommentSectionModel.PublicationStatus.PENDING)
                    .select_related("user", "series")
                    .order_by("-created_at")
                ),
                "pending_articles": list(
                    ArticleBlogModel.objects.filter(
                        publication_status=ArticleBlogModel.PublicationStatus.PENDING
                    )
                    .select_related("author")
                    .order_by("-id")
                ),
                "health_alerts": health_alert_service.get_alerts(self.request.user),
                "health_alert_push_enabled": bool(
                    settings.WEBPUSH_VAPID_PUBLIC_KEY and settings.WEBPUSH_VAPID_PRIVATE_KEY
                ),
                "health_alert_vapid_public_key": settings.WEBPUSH_VAPID_PUBLIC_KEY,
                "health_alert_push_subscribed": MoodReminderSubscription.objects.filter(
                    user=self.request.user
                ).exists(),
            }
        )
        return context


class CoachHealthAlertSettingsView(StaffRequiredMixin, View):
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not is_coach_only(request.user):
            return redirect("register:admin_search")
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        enabled = request.POST.get("enabled") == "true"
        health_alert_service.set_coach_enabled(request.user, enabled)
        messages.success(
            request,
            _("اعلان تغییرات شاگردان فعال شد.") if enabled else _("اعلان تغییرات شاگردان غیرفعال شد."),
        )
        return redirect("register:coach_dashboard")


class HealthAlertReviewView(StaffRequiredMixin, View):
    def post(self, request, pk, *args, **kwargs):
        queryset = AthleteHealthAlert.objects.filter(pk=pk, recipient=request.user)
        if is_coach_only(request.user):
            queryset = queryset.filter(athlete__coach=request.user)
        updated = queryset.update(reviewed_at=timezone.now())
        if updated:
            messages.success(request, _("اعلان به‌عنوان بررسی‌شده ثبت شد."))
        destination = "register:coach_dashboard" if is_coach_only(request.user) else "register:admin_notifications"
        return redirect(destination)
