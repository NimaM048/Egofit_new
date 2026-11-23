"""Coach onboarding, requests, athlete roster, and exercise suggestions."""

from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import TemplateView

from account.admin_forms import (
    AdminCoachApplicationReviewForm,
    AdminExerciseSuggestionReviewForm,
    CoachAthleteCreateForm,
    DailyMoodForm,
)
from account.froms import CoachProfileForm, ExerciseSuggestionForm
from account.models import (
    CoachApplication,
    CoachRequest,
    ExerciseSuggestion,
    MoodReminderSubscription,
)
from account.portal_mixins import (
    AdminRequiredMixin,
    StaffRequiredMixin,
    coach_roster_queryset,
    is_coach_only,
)
from account.selectors.user_selector import UserSelector

from .mixins import AdminPageMixin, StaffUserMixin
from .services import (
    admin_portal_service,
    coach_application_service,
    exercise_suggestion_service,
    health_alert_service,
    mood_service,
)


class AdminUserCoachRequestsReadView(StaffUserMixin, View):
    def post(self, request, user_id):
        count = admin_portal_service.mark_pending_coach_requests_read(user=self.target_user)
        return JsonResponse({"marked_read": count})


class AdminUserCoachRequestDeleteView(StaffUserMixin, View):
    def post(self, request, user_id, pk):
        coach_request = get_object_or_404(CoachRequest, pk=pk, user=self.target_user)
        admin_portal_service.delete_coach_request(coach_request=coach_request)
        messages.success(request, _("پیام کاربر با موفقیت حذف شد."))
        return redirect("register:admin_user_hub", user_id=self.target_user.pk)


class CoachRequestMarkHandledView(StaffRequiredMixin, View):
    def post(self, request, pk):
        queryset = CoachRequest.objects.select_related("user")
        if is_coach_only(request.user):
            coach_request = get_object_or_404(queryset, pk=pk, user__coach=request.user)
        else:
            coach_request = get_object_or_404(queryset, pk=pk)
        admin_portal_service.mark_coach_request_handled(coach_request)
        messages.success(request, _("درخواست به‌عنوان بررسی‌شده علامت خورد."))
        next_url = request.POST.get("next") or reverse("register:admin_search")
        return redirect(next_url)


class CoachRequestPushMeasurementsView(AdminRequiredMixin, View):
    def post(self, request, pk):
        coach_request = get_object_or_404(CoachRequest, pk=pk)
        result = admin_portal_service.save_coach_request_to_database(
            coach_request=coach_request,
            uploaded_by=request.user,
        )
        saved_count = sum(bool(result[key]) for key in ("measurements", "caliper")) + result["health"] + result["attachments"]
        if saved_count:
            messages.success(request, _("اطلاعات این درخواست در جداول مرتبط ذخیره شد."))
        else:
            messages.info(request, _("اطلاعات این درخواست قبلاً ذخیره شده است یا داده‌ای برای ذخیره وجود ندارد."))
        next_url = request.POST.get("next") or reverse("register:admin_search")
        return redirect(next_url)


class AdminCoachApplicationsView(AdminPageMixin, TemplateView):
    template_name = "admin_portal/coach_applications.html"
    active_section = "coach_applications"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        status = (self.request.GET.get("status") or "").strip()
        queryset = coach_application_service.get_all_queryset()
        if status in CoachApplication.Status.values:
            queryset = queryset.filter(status=status)
        context.update(
            {
                "applications": list(queryset),
                "pending_count": coach_application_service.pending_count(),
                "active_status": status,
                "status_choices": CoachApplication.Status.choices,
                "review_form": AdminCoachApplicationReviewForm(),
            }
        )
        return context


class AdminCoachApplicationReviewView(AdminRequiredMixin, View):
    def post(self, request, pk):
        application = get_object_or_404(CoachApplication, pk=pk)
        form = AdminCoachApplicationReviewForm(request.POST)
        if not form.is_valid():
            messages.error(request, _("اطلاعات بررسی معتبر نیست."))
            return redirect("register:admin_coach_applications")
        if form.cleaned_data["action"] == "approve":
            coach_application_service.approve(application=application, actor=request.user)
            messages.success(request, _("درخواست مربی شدن تایید شد."))
        else:
            coach_application_service.reject(
                application=application,
                actor=request.user,
                reason=form.cleaned_data["reason"],
            )
            messages.success(request, _("درخواست رد شد و دلیل برای کاربر ارسال گردید."))
        return redirect("register:admin_coach_applications")


class AdminExerciseSuggestionsView(AdminPageMixin, TemplateView):
    template_name = "admin_portal/exercise_suggestions.html"
    active_section = "exercise_suggestions"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        status = (self.request.GET.get("status") or "").strip()
        queryset = exercise_suggestion_service.get_all_queryset()
        if status in ExerciseSuggestion.Status.values:
            queryset = queryset.filter(status=status)
        context.update(
            {
                "suggestions": list(queryset),
                "pending_count": exercise_suggestion_service.pending_count(),
                "active_status": status,
                "status_choices": ExerciseSuggestion.Status.choices,
                "review_form": AdminExerciseSuggestionReviewForm(),
            }
        )
        return context


class AdminExerciseSuggestionReviewView(AdminRequiredMixin, View):
    def post(self, request, pk):
        suggestion = get_object_or_404(ExerciseSuggestion, pk=pk)
        form = AdminExerciseSuggestionReviewForm(request.POST)
        if not form.is_valid():
            messages.error(request, _("اطلاعات بررسی معتبر نیست."))
            return redirect("register:admin_exercise_suggestions")
        if form.cleaned_data["action"] == "approve":
            exercise_suggestion_service.approve(suggestion=suggestion, actor=request.user)
            messages.success(request, _("پیشنهاد حرکت تایید شد."))
        else:
            exercise_suggestion_service.reject(
                suggestion=suggestion,
                actor=request.user,
                reason=form.cleaned_data["reason"],
            )
            messages.success(request, _("پیشنهاد رد شد و دلیل برای مربی ارسال گردید."))
        return redirect("register:admin_exercise_suggestions")


class CoachDashboardView(StaffRequiredMixin, TemplateView):
    template_name = "account/coach-dashboard.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not is_coach_only(request.user):
            return redirect("register:admin_search")
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        form = DailyMoodForm(request.POST)
        if form.is_valid():
            mood_service.save_entry(request.user, form.cleaned_data)
            messages.success(request, _("روحیات و ضربان قلب شما با موفقیت ثبت شد."))
            return redirect("register:coach_dashboard")
        return self.render_to_response(self.get_context_data(coach_mood_form=form))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        roster = coach_roster_queryset(self.request.user)
        mood_entry, heart_rate_entry = mood_service.get_entry_bundle(self.request.user)
        context.update(
            {
                "athletes": list(roster.order_by("fullname")[:12]),
                "athlete_count": roster.count(),
                "active_section": "coach_dashboard",
                "is_coach_only": True,
                "portal_role": "coach",
                "pending_requests": CoachRequest.objects.filter(
                    user__coach=self.request.user,
                    status=CoachRequest.Status.PENDING,
                ).count(),
                "pending_request_items": list(
                    CoachRequest.objects.filter(
                        user__coach=self.request.user,
                        status=CoachRequest.Status.PENDING,
                    )
                    .select_related("user")
                    .prefetch_related("available_equipment")
                    .order_by("created_at")[:8]
                ),
                "suggestion_count": ExerciseSuggestion.objects.filter(
                    requested_by=self.request.user
                ).count(),
                "health_alerts": health_alert_service.get_alerts(self.request.user),
                "coach_health_alerts_enabled": health_alert_service.is_enabled_for_coach(self.request.user),
                "health_alert_push_enabled": bool(
                    settings.WEBPUSH_VAPID_PUBLIC_KEY and settings.WEBPUSH_VAPID_PRIVATE_KEY
                ),
                "health_alert_vapid_public_key": settings.WEBPUSH_VAPID_PUBLIC_KEY,
                "health_alert_push_subscribed": MoodReminderSubscription.objects.filter(
                    user=self.request.user
                ).exists(),
                "coach_mood_form": kwargs.get(
                    "coach_mood_form",
                    DailyMoodForm(initial=mood_service.get_form_initial(mood_entry, heart_rate_entry)),
                ),
                "coach_mood_chart_data": mood_service.get_chart_data(self.request.user),
                "coach_mood_entries": mood_service.get_entries(self.request.user),
                "coach_heart_rate_entries": mood_service.get_heart_rate_entries(self.request.user),
                "coach_paid_orders": UserSelector.get_paid_orders(self.request.user),
            }
        )
        return context


class CoachAthleteCreateView(StaffRequiredMixin, View):
    template_name = "admin_portal/coach_athlete_create.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not is_coach_only(request.user):
            return redirect("register:admin_search")
        return super().dispatch(request, *args, **kwargs)

    def _render(self, request, form):
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "page_title": _("افزودن ورزشکار"),
                "portal_page_title": _("افزودن ورزشکار"),
                "active_section": "coach_dashboard",
                "portal_role": "coach",
                "is_coach_only": True,
            },
        )

    def get(self, request, *args, **kwargs):
        return self._render(request, CoachAthleteCreateForm())

    def post(self, request, *args, **kwargs):
        form = CoachAthleteCreateForm(request.POST)
        if form.is_valid():
            athlete = admin_portal_service.create_coach_athlete(form=form, coach=request.user)
            messages.success(request, _("ورزشکار با موفقیت به فهرست شاگردان شما اضافه شد."))
            return redirect("register:admin_user_hub", user_id=athlete.pk)
        return self._render(request, form)


class CoachProfileView(StaffRequiredMixin, View):
    template_name = "account/coach-profile.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not is_coach_only(request.user):
            return redirect("register:admin_search")
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        return render(
            request,
            self.template_name,
            {
                "form": CoachProfileForm(instance=request.user),
                "active_section": "coach_profile",
                "is_coach_only": True,
                "portal_role": "coach",
            },
        )

    def post(self, request, *args, **kwargs):
        form = CoachProfileForm(request.POST, request.FILES, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, _("پروفایل مربی به‌روزرسانی شد."))
            return redirect("register:coach_profile")
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "active_section": "coach_profile",
                "is_coach_only": True,
                "portal_role": "coach",
            },
        )


class CoachExerciseSuggestionsView(StaffRequiredMixin, View):
    template_name = "account/coach-exercise-suggestions.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not is_coach_only(request.user):
            return redirect("register:admin_search")
        return super().dispatch(request, *args, **kwargs)

    def _context(self, form=None):
        return {
            "form": form or ExerciseSuggestionForm(),
            "suggestions": list(exercise_suggestion_service.get_for_coach(self.request.user)),
            "active_section": "coach_suggestions",
            "is_coach_only": True,
            "portal_role": "coach",
        }

    def get(self, request, *args, **kwargs):
        return render(request, self.template_name, self._context())

    def post(self, request, *args, **kwargs):
        form = ExerciseSuggestionForm(request.POST, request.FILES)
        if form.is_valid():
            exercise_suggestion_service.submit(coach=request.user, form=form)
            messages.success(request, _("پیشنهاد حرکت برای ادمین ارسال شد."))
            return redirect("register:coach_exercise_suggestions")
        return render(request, self.template_name, self._context(form=form))
