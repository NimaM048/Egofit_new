"""Staff pages for an athlete's health, measurements, and analysis."""

from __future__ import annotations

import jdatetime
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.decorators.cache import never_cache
from django.views.generic import TemplateView

from account.admin_forms import AdminCaliperForm, AdminCircumferenceForm, AdminHealthRecordForm
from account.models import (
    BodyCircumferenceMeasurement,
    CaliperMeasurement,
    MeasurementSource,
    UserHealthRecord,
)
from account.portal_mixins import is_coach_only

from .mixins import StaffUserMixin
from .services import admin_portal_service


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
