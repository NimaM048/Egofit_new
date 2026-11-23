"""Coach and admin review of training recommendations."""

from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.http import urlencode
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import TemplateView

from account.models import TrainingAdjustmentRecommendation, WorkoutSetResult
from account.services.training_recommendation_review_service import (
    TrainingRecommendationReviewService,
)

from .mixins import StaffPageMixin


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
