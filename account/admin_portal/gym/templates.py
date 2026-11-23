"""Staff pages for prescription templates and muscle volume landmarks."""

from __future__ import annotations

import json

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import TemplateView

from account.admin_forms import MuscleVolumeLandmarkForm, ProgramTemplateForm
from account.admin_portal.staff.mixins import AdminPageMixin, StaffPageMixin
from account.portal_mixins import is_coach_only
from account.models import (
    ExerciseMovementType,
    ExerciseSecondaryMovementType,
    Muscle,
    MuscleVolumeLandmark,
    ProgramTemplate,
)
from account.services.program_template_service import ProgramTemplateService


class MuscleVolumeLandmarkListView(AdminPageMixin, TemplateView):
    template_name = "admin_portal/gym/muscle_volume_landmarks.html"
    active_section = "library"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        query = (self.request.GET.get("q") or "").strip()
        muscles = Muscle.objects.all()
        if query:
            muscles = muscles.filter(
                Q(name__icontains=query) | Q(name_en__icontains=query)
            )
        muscles_page = Paginator(muscles.order_by("name", "pk").distinct(), 1).get_page(
            self.request.GET.get("page")
        )
        selected_muscle = muscles_page.object_list.first()
        context["muscle_page"] = muscles_page
        context["search_query"] = query
        context["selected_muscle"] = selected_muscle
        context["landmarks"] = (
            MuscleVolumeLandmark.objects.filter(muscle=selected_muscle)
            .select_related("muscle")
            .order_by("training_goal", "experience_band")
            if selected_muscle
            else MuscleVolumeLandmark.objects.none()
        )
        return context


class MuscleVolumeLandmarkFormView(AdminPageMixin, View):
    template_name = "admin_portal/gym/muscle_volume_landmark_form.html"
    active_section = "library"

    def get(self, request, pk=None):
        instance = get_object_or_404(MuscleVolumeLandmark, pk=pk) if pk else None
        initial = {}
        if not instance and request.GET.get("muscle"):
            try:
                initial["muscle"] = Muscle.objects.filter(pk=int(request.GET["muscle"])).first()
            except (TypeError, ValueError):
                pass
        form = MuscleVolumeLandmarkForm(instance=instance, initial=initial)
        return render(request, self.template_name, self._context(form, instance))

    def post(self, request, pk=None):
        instance = get_object_or_404(MuscleVolumeLandmark, pk=pk) if pk else None
        form = MuscleVolumeLandmarkForm(request.POST, instance=instance)
        if form.is_valid():
            form.save()
            messages.success(request, _("مقادیر حجم هفتگی ذخیره شد."))
            return redirect("register:admin_muscle_volume_landmarks")
        return render(request, self.template_name, self._context(form, instance))

    def _context(self, form, instance):
        goal_labels = dict(MuscleVolumeLandmark._meta.get_field("training_goal").choices)
        experience_labels = dict(MuscleVolumeLandmark.ExperienceBand.choices)
        existing = MuscleVolumeLandmark.objects.select_related("muscle").order_by(
            "muscle__name", "training_goal", "experience_band"
        )
        return {
            "form": form,
            "item": instance,
            "active_section": self.active_section,
            "existing_landmark_options": [
                {
                    "muscle_id": item.muscle_id,
                    "muscle_name": item.muscle.name,
                    "training_goal": item.training_goal,
                    "training_goal_label": str(goal_labels.get(item.training_goal, item.training_goal)),
                    "experience_band": item.experience_band,
                    "experience_band_label": str(experience_labels.get(item.experience_band, item.experience_band)),
                    "edit_url": reverse("register:admin_muscle_volume_landmark_edit", args=[item.pk]),
                }
                for item in existing
            ],
        }


class MuscleVolumeLandmarkDeleteView(AdminPageMixin, View):
    def post(self, request, pk):
        get_object_or_404(MuscleVolumeLandmark, pk=pk).delete()
        messages.success(request, _("مقدار راهنمای حجم حذف شد؛ برای این عضله مقدار پیش‌فرض الگوریتم استفاده می‌شود."))
        return redirect("register:admin_muscle_volume_landmarks")


class ProgramTemplateListView(StaffPageMixin, TemplateView):
    template_name = "admin_portal/gym/program_templates.html"
    active_section = "library"

    def dispatch(self, request, *args, **kwargs):
        if is_coach_only(request.user) and not request.user.can_use_automatic_programming:
            return redirect("register:admin_search")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["templates"] = ProgramTemplateService.accessible_to(self.request.user)
        return context


class ProgramTemplateFormView(StaffPageMixin, View):
    template_name = "admin_portal/gym/program_template_form.html"
    active_section = "library"

    def dispatch(self, request, *args, **kwargs):
        if is_coach_only(request.user) and not request.user.can_use_automatic_programming:
            return redirect("register:admin_search")
        return super().dispatch(request, *args, **kwargs)

    def _instance(self, request, pk):
        if not pk:
            return None
        queryset = ProgramTemplate.objects.all() if request.user.is_admin else ProgramTemplate.objects.filter(owner=request.user)
        return get_object_or_404(queryset, pk=pk)

    def get(self, request, pk=None):
        instance = self._instance(request, pk)
        form = ProgramTemplateForm(instance=instance, actor=request.user)
        return render(request, self.template_name, self._context(form, instance))

    def post(self, request, pk=None):
        instance = self._instance(request, pk)
        form = ProgramTemplateForm(request.POST, instance=instance, actor=request.user)
        if form.is_valid():
            item = form.save(commit=False)
            if item.pk is None:
                item.owner = request.user
            if is_coach_only(request.user):
                item.is_shared_with_enabled_coaches = False
            item.save()
            form.save_m2m()
            messages.success(request, _("قالب برنامه ذخیره شد."))
            return redirect("register:admin_program_templates")
        return render(request, self.template_name, self._context(form, instance))

    def _context(self, form, instance):
        raw_payload = form["payload"].value()
        if isinstance(raw_payload, str):
            try:
                raw_payload = json.loads(raw_payload)
            except (TypeError, ValueError):
                raw_payload = {}
        if not isinstance(raw_payload, dict):
            raw_payload = {}
        return {
            "form": form,
            "item": instance,
            "active_section": self.active_section,
            "payload_initial": raw_payload,
            "movement_type_catalog": [
                {"id": item.pk, "label": item.name}
                for item in ExerciseSecondaryMovementType.objects.order_by("name", "pk")
            ],
            "movement_pattern_catalog": [
                {"id": item.pk, "label": item.name}
                for item in ExerciseMovementType.objects.order_by("name", "pk")
            ],
        }
