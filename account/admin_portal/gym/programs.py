"""Staff pages for managing an athlete's prescribed workout programs."""

from __future__ import annotations

import json

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import TemplateView

from account.admin_forms import WorkoutProgramForm, WorkoutProgramPayloadForm
from account.admin_portal.staff.mixins import StaffUserMixin
from account.portal_mixins import is_coach_only
from account.models import (
    CardioExercise,
    CorrectiveExercise,
    Exercise,
    ExerciseBodyPart,
    ExerciseGoal,
    ExerciseRepetitionType,
    ExerciseRestType,
    ExerciseSetType,
    WorkoutProgramFeedback,
)
from account.services.gym_program_pdf import build_program_pdf
from account.services.automatic_program_service import AutomaticProgramService
from account.services.exercise_goal_service import ExerciseGoalService

from .support import gym_program_service, paginate_gym_queryset


def build_goal_catalog():
    catalog = []
    for goal in ExerciseGoal.objects.order_by("pk"):
        key = ExerciseGoalService.canonical_key(goal.name_en)
        aliases = set(AutomaticProgramService.GOAL_ALIASES.get(key, ()))
        aliases.update({goal.name, goal.name_en})
        if not key:
            continue
        catalog.append(
            {
                "value": key,
                "label": goal.name,
                "aliases": sorted(alias for alias in aliases if alias),
            }
        )
    return catalog


class AdminProgramMixin(StaffUserMixin):
    active_section = "records"

    def _get_program(self, pk=None):
        if not pk:
            return None
        return get_object_or_404(
            gym_program_service.get_program_queryset(),
            pk=pk,
            user=self.target_user,
        )


class AdminProgramListView(AdminProgramMixin, TemplateView):
    template_name = "admin_portal/gym/program_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        programs_page = paginate_gym_queryset(
            self.request,
            gym_program_service.get_program_queryset().filter(user=self.target_user).order_by("-created_at", "-pk"),
        )
        programs = list(programs_page["page_obj"].object_list)
        feedback_by_day = {}
        legacy_feedback_by_program = {}
        for feedback in WorkoutProgramFeedback.objects.filter(
            program_id__in=[program.pk for program in programs]
        ).select_related("day").order_by("-submitted_at", "-pk"):
            if feedback.day_id is None:
                legacy_feedback_by_program.setdefault(feedback.program_id, feedback)
            else:
                feedback_by_day.setdefault((feedback.program_id, feedback.day_id), feedback)

        for program in programs:
            program.session_feedbacks = [
                feedback
                for day in program.days.all()
                if (feedback := feedback_by_day.get((program.pk, day.pk))) is not None
            ]
            program.legacy_feedback = legacy_feedback_by_program.get(program.pk)
        context["programs"] = programs
        context.update(programs_page)
        return context


class AdminProgramFormView(AdminProgramMixin, View):
    template_name = "admin_portal/gym/program_form.html"

    def get(self, request, user_id, pk=None):
        program = self._get_program(pk)
        return render(
            request,
            self.template_name,
            self._context(
                WorkoutProgramForm(instance=program),
                WorkoutProgramPayloadForm(),
                program,
            ),
        )

    def post(self, request, user_id, pk=None):
        program = self._get_program(pk)
        form = WorkoutProgramForm(request.POST, instance=program)
        payload_form = WorkoutProgramPayloadForm(request.POST)
        if form.is_valid() and payload_form.is_valid():
            try:
                gym_program_service.save_program(
                    program_form=form,
                    target_user=self.target_user,
                    prescribed_by=request.user,
                    days_payload=payload_form.cleaned_data["days_json"],
                    correctives_payload=payload_form.cleaned_data.get("correctives_json") or [],
                )
            except ValidationError as exc:
                for error in exc.messages:
                    form.add_error(None, error)
            else:
                messages.success(
                    request,
                    _("برنامه بدنسازی با موفقیت %(action)s شد.")
                    % {"action": _("ویرایش") if program else _("ثبت")},
                )
                return redirect("register:admin_program_list", user_id=self.target_user.pk)

        return render(request, self.template_name, self._context(form, payload_form, program))

    def _context(self, form, payload_form, program):
        raw_payload = {
            "days": [],
            "correctives": [],
        }
        if program is not None and not payload_form.is_bound:
            raw_payload = gym_program_service.serialize_program(program)
        elif payload_form.is_bound:
            raw_payload = {
                "days": self._decode_payload(payload_form.data.get("days_json"), []),
                "correctives": self._decode_payload(payload_form.data.get("correctives_json"), []),
            }
        return {
            "form": form,
            "payload_form": payload_form,
            "program": program,
            "target_user": self.target_user,
            "goal_catalog": build_goal_catalog(),
            "exercise_catalog": [
                {
                    "id": exercise.pk,
                    "name": exercise.name,
                    "muscle": exercise.primary_muscle.name,
                    "body_part_id": exercise.body_part_id,
                    "body_part": exercise.body_part.name,
                }
                for exercise in Exercise.objects.select_related(
                    "primary_muscle", "body_part"
                ).only(
                    "pk",
                    "name",
                    "primary_muscle_id",
                    "primary_muscle__name",
                    "body_part_id",
                    "body_part__name",
                )
            ],
            "cardio_catalog": [
                {
                    "id": exercise.pk,
                    "name": exercise.name,
                    "performance_type": exercise.performance_type,
                    "performance_type_label": exercise.get_performance_type_display(),
                }
                for exercise in CardioExercise.objects.only("pk", "name", "performance_type")
            ],
            "body_part_catalog": [
                {"id": item.pk, "name": item.name}
                for item in ExerciseBodyPart.objects.only("pk", "name")
            ],
            "set_catalog": [
                {"value": item.set_count, "label": str(item)}
                for item in ExerciseSetType.objects.all()
            ],
            "repetition_catalog": [
                {"value": item.reps, "label": str(item)}
                for item in ExerciseRepetitionType.objects.all()
            ],
            "rest_catalog": [
                {"value": item.rest_time, "label": str(item)}
                for item in ExerciseRestType.objects.all()
            ],
            "set_goal_catalog": [
                {"value": item.set_count, "goal": item.goal}
                for item in ExerciseSetType.objects.all()
            ],
            "repetition_goal_catalog": [
                {"value": item.reps, "goal": item.goal}
                for item in ExerciseRepetitionType.objects.all()
            ],
            "rest_goal_catalog": [
                {"value": item.rest_time, "goal": item.goal}
                for item in ExerciseRestType.objects.all()
            ],
            "corrective_catalog": [
                {"id": corrective.pk, "name": corrective.name}
                for corrective in CorrectiveExercise.objects.only("pk", "name")
            ],
            "program_payload": raw_payload,
            "active_section": self.active_section,
            "is_coach_only": is_coach_only(self.request.user),
            "page_title": _("ویرایش برنامه بدنسازی") if program else _("افزودن برنامه بدنسازی"),
        }

    @staticmethod
    def _decode_payload(value, default):
        if not value:
            return default
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return default
        return decoded if isinstance(decoded, list) else default


class AdminProgramDeleteView(AdminProgramMixin, View):
    def post(self, request, user_id, pk):
        program = self._get_program(pk)
        program.delete()
        messages.success(request, _("برنامه بدنسازی حذف شد."))
        return redirect("register:admin_program_list", user_id=self.target_user.pk)


class AdminProgramPdfView(AdminProgramMixin, View):
    def get(self, request, user_id, pk):
        program = self._get_program(pk)
        response = HttpResponse(
            build_program_pdf(program, request=request),
            content_type="application/pdf",
        )
        response["Content-Disposition"] = f'attachment; filename="workout-program-{program.pk}.pdf"'
        return response
