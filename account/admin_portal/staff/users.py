"""Account search, access, and coach roster administration."""

from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.views import PasswordChangeView
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.http import urlencode
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import ListView, TemplateView

from account.admin_forms import (
    AdminBulkCoachAssignForm,
    AdminPersonalInfoForm,
    AdminRoleSetForm,
    AdminCoachRoleToggleForm,
    AdminRoleToggleForm,
    AdminUserCoachForm,
    AdminUserCreateForm,
    AdminUserSearchForm,
)
from account.froms import PasswordChanged
from account.models import MoodReminderSubscription, TrainingAdjustmentRecommendation, User
from account.portal_mixins import AdminRequiredMixin, coach_roster_queryset, is_coach_only
from account.selectors.user_selector import UserSelector
from account.services.training_recommendation_review_service import (
    TrainingRecommendationReviewService,
)

from .mixins import AdminPageMixin, StaffPageMixin, StaffUserMixin
from .services import admin_portal_service, health_alert_service


USER_LIST_SORT_OPTIONS = {
    "fullname": "fullname",
    "-fullname": "-fullname",
    "-id": "-id",
    "coach": "coach__fullname",
    "-coach": "-coach__fullname",
}


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
