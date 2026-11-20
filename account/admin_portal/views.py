from __future__ import annotations

import jdatetime
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.views import PasswordChangeView
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.decorators import method_decorator
from django.utils import timezone
from django.utils.http import urlencode
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.decorators.cache import never_cache
from django.views.generic import ListView, TemplateView

from account.admin_forms import (
    AdminBulkCoachAssignForm,
    AdminCaliperForm,
    AdminCoachApplicationReviewForm,
    AdminCircumferenceForm,
    AdminClientDocumentForm,
    AdminClientMediaForm,
    AdminCommentModerationForm,
    AdminExerciseSuggestionReviewForm,
    AdminPersonalInfoForm,
    AdminRoleSetForm,
    AdminCoachRoleToggleForm,
    AdminRoleToggleForm,
    AdminUserCoachForm,
    AdminUserCreateForm,
    CoachAthleteCreateForm,
    AdminHealthRecordForm,
    AdminUserSearchForm,
    DailyMoodForm,
)
from account.froms import (
    CoachApplicationForm,
    CoachProfileForm,
    ExerciseSuggestionForm,
    PasswordChanged,
)
from account.models import (
    AthleteHealthAlert,
    BirthdaySmsLog,
    BodyCircumferenceMeasurement,
    CaliperMeasurement,
    ClientDocument,
    ClientMedia,
    CoachApplication,
    CoachRequest,
    ExerciseSuggestion,
    MoodReminderSubscription,
    MeasurementSource,
    TrainingAdjustmentRecommendation,
    User,
    UserHealthRecord,
    WorkoutSetResult,
)
from home.models import ArticleBlogModel, Comment, CommentSectionModel, Reply
from home.forms import CoachBlogPostForm
from home.services.content_service import invalidate_blog_caches
from account.portal_mixins import (
    AdminRequiredMixin,
    StaffRequiredMixin,
    coach_roster_queryset,
    is_coach_only,
    staff_portal_role,
)
from account.selectors.user_selector import UserSelector
from account.services.admin_portal_service import AdminPortalService
from account.services.admin_financial_analytics_service import AdminFinancialAnalyticsService
from account.services.coach_application_service import CoachApplicationService
from account.services.exercise_suggestion_service import ExerciseSuggestionService
from account.services.health_alert_service import HealthAlertService
from account.services.mood_service import MoodService
from account.services.notification_service import NotificationService
from account.services.training_recommendation_review_service import TrainingRecommendationReviewService

admin_portal_service = AdminPortalService()
coach_application_service = CoachApplicationService()
exercise_suggestion_service = ExerciseSuggestionService()
health_alert_service = HealthAlertService()
mood_service = MoodService()

USER_LIST_SORT_OPTIONS = {
    "fullname": "fullname",
    "-fullname": "-fullname",
    "-id": "-id",
    "coach": "coach__fullname",
    "-coach": "-coach__fullname",
}


class AdminPageMixin(AdminRequiredMixin):
    active_section = "search"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("active_section", self.active_section)
        context.setdefault("portal_role", "admin")
        context.setdefault("is_coach_only", False)
        return context


class StaffPageMixin(StaffRequiredMixin):
    """Shared chrome for pages both administrators and coaches may open.

    ``portal_role`` lets the templates hide administrator-only navigation and
    actions while keeping the same look and layout for coaches.
    """

    active_section = "search"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("active_section", self.active_section)
        context["portal_role"] = staff_portal_role(self.request.user)
        context["is_coach_only"] = is_coach_only(self.request.user)
        return context


class StaffUserMixin(StaffPageMixin):
    """Resolve ``target_user`` and forbid coaches from opening other athletes.

    A pure coach may only open the record of an athlete assigned to them; an
    administrator keeps unrestricted access.
    """

    def dispatch(self, request, *args, **kwargs):
        if is_coach_only(request.user):
            self.target_user = get_object_or_404(
                User,
                pk=kwargs["user_id"],
                coach=request.user,
                is_active=True,
                is_admin=False,
                is_coach=False,
            )
        else:
            self.target_user = get_object_or_404(User, pk=kwargs["user_id"])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["target_user"] = self.target_user
        return context


class AdminSearchView(StaffPageMixin, TemplateView):
    template_name = "admin_portal/search.html"
    active_section = "search"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        query = (self.request.GET.get("query") or "").strip()
        form = AdminUserSearchForm(initial={"query": query})
        if is_coach_only(self.request.user):
            roster = coach_roster_queryset(self.request.user).select_related("coach")
            if query:
                roster = UserSelector.search_clients(roster, query)
            context.update(
                {
                    "form": form,
                    "search_query": query,
                    "search_results": list(roster.order_by("fullname")[:50]),
                    "has_searched": bool(query),
                    "roster_count": coach_roster_queryset(self.request.user).count(),
                }
            )
            return context
        results = admin_portal_service.search_users(query=query) if query else []
        context.update(admin_portal_service.get_dashboard_context())
        context.update(
            {
                "form": form,
                "search_query": query,
                "search_results": results,
                "has_searched": bool(query),
            }
        )
        context.update(
            {
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


class AdminUserListView(AdminPageMixin, ListView):
    model = User
    template_name = "admin_portal/user_list.html"
    context_object_name = "clients"
    active_section = "clients"
    paginate_by = 20

    def get_queryset(self):
        queryset = User.objects.select_related("coach")
        query = (self.request.GET.get("query") or "").strip()
        queryset = UserSelector.search_clients(queryset, query)
        sort = self.request.GET.get("sort") or "fullname"
        return queryset.order_by(USER_LIST_SORT_OPTIONS.get(sort, "fullname"))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        search_query = (self.request.GET.get("query") or "").strip()
        context.update(
            {
                "form": AdminUserSearchForm(initial={"query": search_query}),
                "search_query": search_query,
                "current_sort": self.request.GET.get("sort") or "fullname",
            }
        )
        return context


class AdminUserCreateView(AdminPageMixin, View):
    template_name = "admin_portal/user_create.html"
    active_section = "clients"

    def get(self, request):
        return render(request, self.template_name, self._context(AdminUserCreateForm()))

    def post(self, request):
        form = AdminUserCreateForm(request.POST)
        if form.is_valid():
            user = admin_portal_service.create_user(form=form)
            messages.success(request, _("کاربر جدید با موفقیت اضافه شد."))
            return redirect("register:admin_circumference", user_id=user.pk)
        return render(request, self.template_name, self._context(form))

    def _context(self, form):
        return {
            "form": form,
            "active_section": self.active_section,
            "page_title": _("افزودن کاربر جدید"),
        }


class AdminUserDeleteView(AdminRequiredMixin, View):
    def post(self, request, user_id):
        target = get_object_or_404(User, pk=user_id, is_admin=False)
        success, error = admin_portal_service.delete_user(target)
        if success:
            messages.success(request, _("کاربر با موفقیت حذف شد."))
        else:
            messages.error(request, error)
        return redirect("register:admin_user_list")


class AdminUserMixin(AdminPageMixin):
    def dispatch(self, request, *args, **kwargs):
        self.target_user = get_object_or_404(User, pk=kwargs["user_id"])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["target_user"] = self.target_user
        return context


class AdminUserHubView(StaffUserMixin, TemplateView):
    template_name = "admin_portal/user_hub.html"
    active_section = "records"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            admin_portal_service.get_user_summary_context(
                self.target_user,
                workout_record_query=self.request.GET.get("exercise"),
            )
        )
        recommendation_service = TrainingRecommendationReviewService()
        context["training_recommendation_pending_count"] = recommendation_service.pending_group_queryset(
            self.request.user
        ).filter(user=self.target_user).count()
        recommendations = self.target_user.training_adjustment_recommendations
        context["training_recommendation_history_count"] = recommendations.exclude(
            status=TrainingAdjustmentRecommendation.Status.PENDING
        ).count()
        return context


class TrainingRecommendationQueueView(StaffPageMixin, TemplateView):
    template_name = "admin_portal/training_recommendations.html"
    active_section = "recommendations"
    page_size = 25

    TABS = {"pending", "approved", "rejected", "merged", "archive"}

    @staticmethod
    def _apply_filters(queryset, query, user_id):
        if user_id:
            queryset = queryset.filter(user_id=user_id)
        if query:
            queryset = queryset.filter(
                Q(user__fullname__icontains=query)
                | Q(user__phone__icontains=query)
                | Q(movement_name__icontains=query)
                | Q(exercise__name__icontains=query)
            )
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        service = TrainingRecommendationReviewService()
        tab = self.request.GET.get("tab", "pending")
        if tab not in self.TABS:
            tab = "pending"
        query = " ".join((self.request.GET.get("q") or "").split())
        user_id = (self.request.GET.get("user") or "").strip()
        if not user_id.isdigit():
            user_id = ""

        visible = service.visible_queryset(self.request.user).select_related(
            "user", "exercise", "source_session", "reviewed_by", "superseded_by", "applied_to_program"
        )
        pending_groups = self._apply_filters(
            service.pending_group_queryset(self.request.user), query, user_id
        )
        history = self._apply_filters(
            visible.exclude(status=TrainingAdjustmentRecommendation.Status.PENDING), query, user_id
        )
        if tab == "pending":
            queryset = pending_groups
        elif tab == "approved":
            queryset = history.filter(status__in=service.APPROVED_STATUSES)
        elif tab == "rejected":
            queryset = history.filter(status=TrainingAdjustmentRecommendation.Status.REJECTED)
        elif tab == "merged":
            queryset = history.filter(status=TrainingAdjustmentRecommendation.Status.SUPERSEDED)
        else:
            queryset = history
        queryset = queryset.order_by("-created_at", "-pk")

        tabs = [
            {"key": "pending", "label": _("در انتظار"), "count": pending_groups.count()},
            {"key": "approved", "label": _("تأیید یا اصلاح‌شده"), "count": history.filter(status__in=service.APPROVED_STATUSES).count()},
            {"key": "rejected", "label": _("ردشده"), "count": history.filter(status=TrainingAdjustmentRecommendation.Status.REJECTED).count()},
            {"key": "merged", "label": _("ادغام‌شده"), "count": history.filter(status=TrainingAdjustmentRecommendation.Status.SUPERSEDED).count()},
            {"key": "archive", "label": _("همهٔ سوابق"), "count": history.count()},
        ]
        for item in tabs:
            item["url"] = self._page_url(item["key"], query, user_id, 1)
        paginator = Paginator(queryset, self.page_size)
        selected_id = (self.request.GET.get("selected") or "").strip()
        selected = queryset.filter(pk=selected_id).first() if selected_id.isdigit() else None
        if selected:
            ahead = queryset.filter(
                Q(created_at__gt=selected.created_at)
                | Q(created_at=selected.created_at, pk__gt=selected.pk)
            ).count()
            requested_page = ahead // self.page_size + 1
        else:
            requested_page = self.request.GET.get("page", 1)
        page_obj = paginator.get_page(requested_page)
        if selected is None:
            selected = page_obj.object_list[0] if page_obj.object_list else None

        for row in page_obj.object_list:
            row.queue_select_url = self._page_url(tab, query, user_id, page_obj.number, row.pk)
        selected_group = []
        set_results = []
        if selected:
            if tab == "pending":
                selected_group = list(service.pending_group_records(self.request.user, selected))
            else:
                selected_group = [selected]
            session_ids = {selected.source_session_id}
            for item in selected_group:
                session_ids.update((item.evidence or {}).get("session_ids", []))
            set_results = list(
                WorkoutSetResult.objects.filter(
                    session_id__in=session_ids,
                    exercise_id=selected.exercise_id,
                    slot=selected.slot,
                )
                .select_related("session")
                .order_by("-session__started_at", "-session_id", "set_number")
            )

        context.update(
            {
                "page_title": _("صف بازبینی پیشنهادهای تمرین"),
                "recommendation_tab": tab,
                "recommendation_tabs": tabs,
                "recommendation_rows": page_obj.object_list,
                "recommendation_page": page_obj,
                "selected_recommendation": selected,
                "selected_recommendation_group": selected_group,
                "selected_set_results": set_results,
                "selected_is_pending": bool(selected and selected.status == TrainingAdjustmentRecommendation.Status.PENDING),
                "search_query": query,
                "user_filter": user_id,
                "queue_filter_action": self.request.path,
                "queue_previous_url": self._page_url(tab, query, user_id, page_obj.previous_page_number()) if page_obj.has_previous() else "",
                "queue_next_url": self._page_url(tab, query, user_id, page_obj.next_page_number()) if page_obj.has_next() else "",
            }
        )
        return context

    @staticmethod
    def _page_url(tab, query, user_id, page, selected=None):
        params = {"tab": tab, "page": page}
        if query:
            params["q"] = query
        if user_id:
            params["user"] = user_id
        if selected:
            params["selected"] = selected
        return f"{reverse('register:admin_training_recommendations')}?{urlencode(params)}"


class TrainingRecommendationReviewView(StaffPageMixin, View):
    """Review the latest pending recommendation and archive older duplicates."""

    def post(self, request, recommendation_id, user_id=None):
        service = TrainingRecommendationReviewService()
        visible = service.visible_queryset(request.user)
        if user_id is not None:
            get_object_or_404(visible, pk=recommendation_id, user_id=user_id)

        status = request.POST.get("status")
        from account.services.training_data_service import TrainingDataService

        try:
            reviewed_load = TrainingDataService._decimal(
                request.POST.get("reviewed_load_kg"), "reviewed_load_kg"
            )
            recommendation, merged_count = service.review(
                staff_user=request.user,
                recommendation_id=recommendation_id,
                status=status,
                reviewed_load=reviewed_load,
                coach_note=(request.POST.get("coach_note") or "").strip(),
            )
        except TrainingAdjustmentRecommendation.DoesNotExist:
            return get_object_or_404(visible, pk=recommendation_id)
        except ValidationError as exc:
            message = exc.messages[0] if getattr(exc, "messages", None) else str(exc)
            messages.error(request, message)
            return self._redirect_to_queue(request, selected_id=recommendation_id)

        if merged_count:
            messages.success(
                request,
                _("تصمیم ثبت شد؛ پیشنهادهای قدیمی‌تر همین حرکت در سوابق به این مورد پیوند خوردند."),
            )
        else:
            messages.success(request, _("تصمیم ثبت شد و پیشنهاد از صف انتظار خارج شد."))

        remaining = service.pending_group_queryset(request.user)
        query = " ".join((request.POST.get("q") or "").split())
        user_filter = (request.POST.get("user") or "").strip()
        if user_filter.isdigit():
            remaining = remaining.filter(user_id=user_filter)
        if query:
            remaining = remaining.filter(
                Q(user__fullname__icontains=query)
                | Q(user__phone__icontains=query)
                | Q(movement_name__icontains=query)
                | Q(exercise__name__icontains=query)
            )
        next_recommendation = remaining.order_by("-created_at", "-pk").first()
        return self._redirect_to_queue(
            request,
            selected_id=next_recommendation.pk if next_recommendation else None,
            tab="pending",
        )

    @staticmethod
    def _redirect_to_queue(request, *, selected_id=None, tab="pending"):
        params = {"tab": tab}
        query = " ".join((request.POST.get("q") or "").split())
        user_filter = (request.POST.get("user") or "").strip()
        if query:
            params["q"] = query
        if user_filter.isdigit():
            params["user"] = user_filter
        if selected_id:
            params["selected"] = selected_id
        return redirect(f"{reverse('register:admin_training_recommendations')}?{urlencode(params)}")


class AdminUserFilesView(StaffUserMixin, TemplateView):
    template_name = "admin_portal/user_files.html"
    active_section = "records"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "client_documents": self.target_user.client_documents.select_related("uploaded_by").all(),
                "client_media": self.target_user.client_media.select_related("uploaded_by").all(),
                "page_title": _("فایل‌های کاربر"),
            }
        )
        return context


class AdminUserFileDeleteView(StaffUserMixin, View):
    def post(self, request, user_id, file_kind, pk):
        if file_kind == "document":
            instance = get_object_or_404(ClientDocument, pk=pk, user=self.target_user)
            admin_portal_service.delete_client_document(document=instance)
        elif file_kind == "media":
            instance = get_object_or_404(ClientMedia, pk=pk, user=self.target_user)
            admin_portal_service.delete_client_media(media=instance)
        else:
            return JsonResponse({"detail": "نوع فایل معتبر نیست."}, status=404)
        messages.success(request, _("فایل با موفقیت حذف شد."))
        return redirect("register:admin_user_files", user_id=self.target_user.pk)


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


class AdminPersonalInfoView(StaffUserMixin, View):
    template_name = "admin_portal/personal_info.html"
    active_section = "records"

    def get(self, request, user_id):
        form = AdminPersonalInfoForm(instance=self.target_user)
        return render(request, self.template_name, self._context(form))

    def post(self, request, user_id):
        form = AdminPersonalInfoForm(request.POST, instance=self.target_user)
        if form.is_valid():
            form.save()
            messages.success(request, _("اطلاعات شخصی با موفقیت ثبت شد."))
            return redirect("register:admin_user_hub", user_id=self.target_user.pk)
        return render(request, self.template_name, self._context(form))

    def _context(self, form):
        return {
            "form": form,
            "target_user": self.target_user,
            "active_section": self.active_section,
            "page_title": _("ثبت اطلاعات شخصی"),
        }


class AdminHealthRecordView(StaffUserMixin, View):
    template_name = "admin_portal/health_details.html"
    active_section = "records"

    def get(self, request, user_id, pk=None):
        instance = get_object_or_404(UserHealthRecord, pk=pk, user=self.target_user) if pk else None
        form = AdminHealthRecordForm(instance=instance)
        self._scope_coach_form(form)
        return render(request, self.template_name, self._context(form, instance))

    def post(self, request, user_id, pk=None):
        instance = get_object_or_404(UserHealthRecord, pk=pk, user=self.target_user) if pk else None
        form = AdminHealthRecordForm(request.POST, instance=instance)
        self._scope_coach_form(form)
        if form.is_valid():
            if is_coach_only(request.user):
                if form.cleaned_data.get("abnormality_type"):
                    form.instance.title = form.cleaned_data["abnormality_type"].name
                else:
                    form.instance.title = dict(form.fields["category"].choices).get(form.cleaned_data["category"], "سابقه تمرینی")
            admin_portal_service.save_health_record(user=self.target_user, form=form)
            messages.success(request, _("اطلاعات سلامت کاربر با موفقیت ثبت شد."))
            return redirect("register:admin_health", user_id=self.target_user.pk)
        return render(request, self.template_name, self._context(form, instance))

    def _context(self, form, instance=None):
        context = admin_portal_service.get_user_health_context(self.target_user)
        coach_scoped = is_coach_only(self.request.user)
        if coach_scoped:
            context["health_records"] = [
                record for record in context["health_records"]
                if record.status == UserHealthRecord.Status.ACTIVE
                and record.category in {UserHealthRecord.CategoryChoices.INJURY, UserHealthRecord.CategoryChoices.ABNORMALITY}
            ]
        context.update({
            "form": form,
            "target_user": self.target_user,
            "active_section": self.active_section,
            "page_title": _("ثبت آسیب‌ها و جزئیات"),
            "is_edit": bool(instance),
            "edit_pk": instance.pk if instance else None,
            "coach_scoped_health": coach_scoped,
            "has_health_title_field": "title" in form.fields,
        })
        return context

    def _scope_coach_form(self, form):
        if not is_coach_only(self.request.user):
            return
        form.fields["category"].choices = tuple(
            choice for choice in form.fields["category"].choices
            if choice[0] in {UserHealthRecord.CategoryChoices.INJURY, UserHealthRecord.CategoryChoices.ABNORMALITY}
        )
        form.fields.pop("details", None)
        form.fields.pop("title", None)


class AdminHealthRecordDeleteView(StaffUserMixin, View):
    def post(self, request, user_id, pk):
        queryset = UserHealthRecord.objects.filter(user=self.target_user)
        if is_coach_only(request.user):
            queryset = queryset.filter(category__in=(UserHealthRecord.CategoryChoices.INJURY, UserHealthRecord.CategoryChoices.ABNORMALITY))
        record = get_object_or_404(queryset, pk=pk)
        record.delete()
        messages.success(request, _("سابقه سلامت حذف شد."))
        return redirect("register:admin_health", user_id=self.target_user.pk)


class AdminCircumferenceView(StaffUserMixin, View):
    template_name = "admin_portal/circumference.html"
    active_section = "records"

    def get(self, request, user_id):
        instance = admin_portal_service.get_or_create_circumference(self.target_user)
        form = AdminCircumferenceForm(instance=instance)
        return render(request, self.template_name, self._context(form))

    def post(self, request, user_id):
        instance = admin_portal_service.get_or_create_circumference(self.target_user)
        form = AdminCircumferenceForm(request.POST, instance=instance)
        if form.is_valid():
            admin_portal_service.save_circumference(
                user=self.target_user,
                form=form,
                actor=self.request.user,
                source=MeasurementSource.ADMIN,
            )
            messages.success(request, _("اندازه‌گیری محیط بدن ثبت شد."))
            return redirect("register:admin_user_hub", user_id=self.target_user.pk)
        return render(request, self.template_name, self._context(form))

    def _context(self, form):
        return {
            "form": form,
            "target_user": self.target_user,
            "active_section": self.active_section,
            "page_title": _("ثبت اندازه‌گیری محیط بدن"),
            "is_edit": False,
        }


class AdminCircumferenceEditView(StaffUserMixin, View):
    template_name = "admin_portal/circumference.html"
    active_section = "records"

    def get(self, request, user_id, pk):
        instance = get_object_or_404(BodyCircumferenceMeasurement, pk=pk, user=self.target_user)
        form = AdminCircumferenceForm(instance=instance)
        return render(request, self.template_name, self._context(form, instance))

    def post(self, request, user_id, pk):
        instance = get_object_or_404(BodyCircumferenceMeasurement, pk=pk, user=self.target_user)
        form = AdminCircumferenceForm(request.POST, instance=instance)
        if form.is_valid():
            admin_portal_service.update_circumference(form=form)
            messages.success(request, _("اندازه‌گیری به‌روزرسانی شد."))
            return redirect("register:admin_measurement_history", user_id=self.target_user.pk)
        return render(request, self.template_name, self._context(form, instance))

    def _context(self, form, instance):
        return {
            "form": form,
            "target_user": self.target_user,
            "active_section": self.active_section,
            "page_title": _("ویرایش اندازه‌گیری"),
            "is_edit": True,
            "edit_pk": instance.pk,
        }


class AdminCaliperView(StaffUserMixin, View):
    template_name = "admin_portal/caliper.html"
    active_section = "records"

    def get(self, request, user_id):
        instance = admin_portal_service.get_or_create_caliper(self.target_user)
        form = AdminCaliperForm(instance=instance)
        return render(request, self.template_name, self._context(form))

    def post(self, request, user_id):
        instance = admin_portal_service.get_or_create_caliper(self.target_user)
        form = AdminCaliperForm(request.POST, instance=instance)
        if form.is_valid():
            admin_portal_service.save_caliper(
                user=self.target_user,
                form=form,
                actor=self.request.user,
                source=MeasurementSource.ADMIN,
            )
            messages.success(request, _("اندازه‌گیری کالیپر ثبت شد."))
            return redirect("register:admin_user_hub", user_id=self.target_user.pk)
        return render(request, self.template_name, self._context(form))

    def _context(self, form):
        return {
            "form": form,
            "target_user": self.target_user,
            "active_section": self.active_section,
            "page_title": _("ثبت کالیپر (Caliper)"),
            "is_edit": False,
        }


class AdminCaliperEditView(StaffUserMixin, View):
    template_name = "admin_portal/caliper.html"
    active_section = "records"

    def get(self, request, user_id, pk):
        instance = get_object_or_404(CaliperMeasurement, pk=pk, user=self.target_user)
        form = AdminCaliperForm(instance=instance)
        return render(request, self.template_name, self._context(form, instance))

    def post(self, request, user_id, pk):
        instance = get_object_or_404(CaliperMeasurement, pk=pk, user=self.target_user)
        form = AdminCaliperForm(request.POST, instance=instance)
        if form.is_valid():
            admin_portal_service.update_caliper(form=form)
            messages.success(request, _("اندازه‌گیری به‌روزرسانی شد."))
            return redirect("register:admin_measurement_history", user_id=self.target_user.pk)
        return render(request, self.template_name, self._context(form, instance))

    def _context(self, form, instance):
        return {
            "form": form,
            "target_user": self.target_user,
            "active_section": self.active_section,
            "page_title": _("ویرایش اندازه‌گیری"),
            "is_edit": True,
            "edit_pk": instance.pk,
        }


def _measurement_date_label(record) -> str:
    if record.measured_at_jalali:
        return record.measured_at_jalali
    local = timezone.localtime(record.recorded_at)
    return jdatetime.datetime.fromgregorian(datetime=local).strftime("%Y/%m/%d")


class AdminMeasurementHistoryView(StaffUserMixin, TemplateView):
    template_name = "admin_portal/measurement_history.html"
    active_section = "records"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        entries = [
            {
                "kind": "circumference",
                "label": _("اندازه‌گیری محیط بدن"),
                "date": _measurement_date_label(record),
                "edit_url": reverse("register:admin_circumference_edit", args=[self.target_user.pk, record.pk]),
            }
            for record in self.target_user.circumference_records.all()
        ] + [
            {
                "kind": "caliper",
                "label": _("اندازه‌گیری کالیپر"),
                "date": _measurement_date_label(record),
                "edit_url": reverse("register:admin_caliper_edit", args=[self.target_user.pk, record.pk]),
            }
            for record in self.target_user.caliper_records.all()
        ]
        entries.sort(key=lambda item: item["date"], reverse=True)
        context["measurement_entries"] = entries
        return context


@method_decorator(never_cache, name="dispatch")
class AdminUserAnalysisView(StaffUserMixin, TemplateView):
    template_name = "admin_portal/analysis.html"
    active_section = "analysis"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            admin_portal_service.get_analysis_context(
                self.target_user,
                metric=self.request.GET.get("metric"),
                start=self.request.GET.get("start"),
                end=self.request.GET.get("end"),
                circ_date=self.request.GET.get("circ_date"),
                body_fat_formula=self.request.GET.get("body_fat_formula"),
                use_full_chart_range=True,
            )
        )
        selected_body_fat_formula = context.get("analysis_selected_body_fat_formula")
        start = self.request.GET.get("start")
        end = self.request.GET.get("end")
        context.update(
            admin_portal_service.get_analysis_dashboard_data(
                self.target_user,
                body_fat_formula=selected_body_fat_formula,
                start=start,
                end=end,
            )
        )
        context["analysis_metric_series"] = admin_portal_service.get_analysis_metric_series(
            self.target_user,
            body_fat_formula=selected_body_fat_formula,
            start=start,
            end=end,
        )
        context["page_title"] = self.target_user.fullname
        return context


class AdminCoachManagementView(AdminPageMixin, View):
    template_name = "admin_portal/coach_management.html"
    active_section = "coaches"

    def get(self, request):
        query = (request.GET.get("query") or "").strip()
        return render(request, self.template_name, self._context(query=query))

    def post(self, request):
        action = request.POST.get("action")
        if action == "bulk_assign":
            form = AdminBulkCoachAssignForm(request.POST)
            if form.is_valid():
                coach = form.cleaned_data["coach"]
                count = admin_portal_service.assign_coach_to_all(coach)
                messages.success(
                    request,
                    _("مربی «%(coach)s» برای %(count)s کاربر تنظیم شد.") % {"coach": coach.fullname, "count": count},
                )
                return self._redirect_to_search(request)
            return render(
                request,
                self.template_name,
                self._context(query=request.POST.get("query", ""), bulk_form=form),
            )

        if action == "update_user":
            user_id = request.POST.get("user_id")
            target = get_object_or_404(User, pk=user_id, is_admin=False, is_coach=False)
            form = AdminUserCoachForm(request.POST)
            if form.is_valid():
                admin_portal_service.assign_coach_to_user(target, form.cleaned_data.get("coach"))
                messages.success(
                    request,
                    _("مربی کاربر «%(name)s» به‌روزرسانی شد.") % {"name": target.fullname},
                )
            else:
                messages.error(request, _("انتخاب مربی معتبر نیست."))
            return self._redirect_to_search(request)

        if action == "set_automatic_programming":
            coach = get_object_or_404(
                User,
                pk=request.POST.get("coach_id"),
                is_coach=True,
                is_admin=False,
            )
            enabled_value = request.POST.get("enabled")
            if enabled_value not in {"0", "1"}:
                messages.error(request, _("وضعیت دسترسی معتبر نیست."))
                return self._redirect_to_search(request)
            coach.can_use_automatic_programming = enabled_value == "1"
            coach.save(update_fields=["can_use_automatic_programming"])
            status = _("فعال") if coach.can_use_automatic_programming else _("غیرفعال")
            messages.success(
                request,
                _("دسترسی برنامه‌ریزی خودکار برای مربی «%(name)s» %(status)s شد.")
                % {"name": coach.fullname, "status": status},
            )
            return self._redirect_to_search(request)

        return self._redirect_to_search(request)

    def _redirect_to_search(self, request):
        query = " ".join(str(request.POST.get("query", "")).split())
        url = reverse("register:admin_coach_management")
        if query:
            url = f"{url}?{urlencode({'query': query})}"
        return redirect(url)

    def _context(self, query="", bulk_form=None):
        context = admin_portal_service.get_coach_management_context(query=query)
        if bulk_form is not None:
            context["bulk_form"] = bulk_form
        context.update(
            {
                "active_section": self.active_section,
                "page_title": _("مدیریت مربیان"),
            }
        )
        return context


class AdminPasswordChangeView(StaffPageMixin, PasswordChangeView):
    form_class = PasswordChanged
    template_name = "admin_portal/change_password.html"
    success_url = reverse_lazy("register:admin_search")
    active_section = "settings"

    def form_valid(self, form):
        response = super().form_valid(form)
        update_session_auth_hash(self.request, form.user)
        messages.success(self.request, _("رمز عبور شما با موفقیت تغییر کرد."))
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_title"] = _("تغییر رمز عبور")
        return context


class AdminUploadView(StaffUserMixin, View):
    template_name = "admin_portal/upload.html"
    active_section = "search"
    hide_admin_chrome = True

    def get(self, request, user_id):
        return render(request, self.template_name, self._context(AdminClientMediaForm(), AdminClientDocumentForm()))

    def post(self, request, user_id):
        upload_kind = request.POST.get("upload_kind")

        if upload_kind == "document":
            document_form = AdminClientDocumentForm(request.POST, request.FILES)
            media_form = AdminClientMediaForm()
            if document_form.is_valid():
                admin_portal_service.save_document(user=self.target_user, uploaded_by=request.user, form=document_form)
                messages.success(request, _("برنامه/فایل با موفقیت آپلود شد."))
                return redirect("register:admin_user_hub", user_id=self.target_user.pk)
        else:
            media_form = AdminClientMediaForm(request.POST, request.FILES)
            document_form = AdminClientDocumentForm()
            if media_form.is_valid():
                if not media_form.cleaned_data.get("image") and not media_form.cleaned_data.get("video"):
                    media_form.add_error(None, _("حداقل یک فایل تصویر یا ویدیو انتخاب کنید."))
                else:
                    admin_portal_service.save_media(user=self.target_user, uploaded_by=request.user, form=media_form)
                    messages.success(request, _("فایل با موفقیت آپلود شد."))
                    return redirect("register:admin_search")

        return render(request, self.template_name, self._context(media_form, document_form))

    def _context(self, media_form, document_form):
        return {
            "form": media_form,
            "document_form": document_form,
            "target_user": self.target_user,
            "active_section": self.active_section,
            "hide_admin_chrome": self.hide_admin_chrome,
            "page_title": _("آپلود فایل"),
        }


class AdminRoleToggleView(AdminRequiredMixin, View):
    def post(self, request, user_id):
        target_user = get_object_or_404(User, pk=user_id)
        form = AdminRoleToggleForm(request.POST)
        if form.is_valid():
            try:
                admin_portal_service.toggle_admin_role(
                    actor=request.user,
                    target=target_user,
                    make_admin=form.cleaned_data["is_admin"],
                )
                messages.success(request, _("نقش کاربر به‌روزرسانی شد."))
            except ValueError as exc:
                messages.error(request, str(exc))
        return redirect(f"{reverse('register:admin_search')}?query={target_user.fullname}")


class AdminCoachRoleToggleView(AdminRequiredMixin, View):
    def post(self, request, user_id):
        target_user = get_object_or_404(User, pk=user_id)
        form = AdminCoachRoleToggleForm(request.POST)
        if form.is_valid():
            try:
                admin_portal_service.toggle_coach_role(
                    actor=request.user,
                    target=target_user,
                    make_coach=form.cleaned_data["is_coach"],
                )
                messages.success(request, _("نقش مربی کاربر به‌روزرسانی شد."))
            except ValueError as exc:
                messages.error(request, str(exc))
        return redirect(f"{reverse('register:admin_search')}?query={target_user.fullname}")


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


class AdminRoleSetView(AdminRequiredMixin, View):
    """Assign admin / coach / athlete in one atomic action."""

    def post(self, request, user_id):
        target_user = get_object_or_404(User, pk=user_id)
        form = AdminRoleSetForm(request.POST)
        if not form.is_valid():
            messages.error(request, _("نقش انتخاب‌شده معتبر نیست."))
            return redirect(f"{reverse('register:admin_search')}?query={target_user.fullname}")

        role = form.cleaned_data["role"]
        if target_user.pk == request.user.pk and role != User.Role.ADMIN:
            messages.error(request, _("امکان حذف نقش ادمین از حساب خودتان وجود ندارد."))
            return redirect(f"{reverse('register:admin_search')}?query={target_user.fullname}")

        with transaction.atomic():
            target_user.set_role(role)
            update_fields = ["is_admin", "is_coach"]
            if role != User.Role.COACH and target_user.can_use_automatic_programming:
                target_user.can_use_automatic_programming = False
                update_fields.append("can_use_automatic_programming")
            target_user.save(update_fields=update_fields)
            if role != User.Role.COACH and role != User.Role.ADMIN:
                User.objects.filter(coach=target_user).update(coach=None)
        messages.success(request, _("نقش کاربر با موفقیت تغییر کرد."))
        return redirect(f"{reverse('register:admin_search')}?query={target_user.fullname}")


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


class AdminAnalyticsView(AdminPageMixin, TemplateView):
    """Site-wide people and financial analytics for the administrator."""

    template_name = "admin_portal/analytics.html"
    active_section = "analytics"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        finance_date_error = ""
        try:
            financial_analytics = AdminFinancialAnalyticsService.dashboard_data(
                self.request.GET.get("from_date"),
                self.request.GET.get("to_date"),
            )
        except ValueError as exc:
            finance_date_error = str(exc)
            financial_analytics = AdminFinancialAnalyticsService.dashboard_data()
        athletes = User.objects.filter(is_admin=False, is_coach=False, is_active=True)
        coaches = User.objects.filter(is_coach=True, is_active=True)
        coach_rows = list(
            coaches.annotate(athlete_count=Count("athletes")).order_by("-athlete_count", "fullname")
        )
        max_athletes = max((coach.athlete_count for coach in coach_rows), default=0) or 1
        for coach in coach_rows:
            coach.bar_percent = round(coach.athlete_count * 100 / max_athletes, 1)
        context.update(
            {
                "total_athletes": athletes.count(),
                "total_coaches": coaches.count(),
                "total_admins": User.objects.filter(is_admin=True, is_active=True).count(),
                "assigned_athletes": athletes.filter(coach__isnull=False).count(),
                "unassigned_athletes": athletes.filter(coach__isnull=True).count(),
                "total_measurements": BodyCircumferenceMeasurement.objects.count(),
                "total_caliper": CaliperMeasurement.objects.count(),
                "coach_rows": coach_rows,
                "financial_analytics": financial_analytics,
                "finance_date_error": finance_date_error,
                "athlete_rows": list(
                    athletes.select_related("coach").order_by("-id")[:50]
                ),
            }
        )
        return context


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
