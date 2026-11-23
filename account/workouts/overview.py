"""Read and display an athlete's prescribed workout plans."""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView

from account.models import WorkoutProgramFeedback
from account.portal_mixins import UserPortalRequiredMixin
from account.services.training_periodization_service import TrainingPeriodizationService
from account.view_mixins import AccountPageMixin

from .support import gym_program_service, _has_workout_program_access
from .program_context import attach_performance_context, attach_best_record_context


class ProfileWorkoutProgramsView(AccountPageMixin, UserPortalRequiredMixin, TemplateView):
    template_name = "account/profile-workout-programs.html"
    active_section = "workout"
    account_title = _("برنامه بدنسازی")
    account_description = _("برنامه تمرینی تجویز‌شده، حرکت‌ها و نکات مربی را آنلاین دنبال کنید.")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        programs = list(gym_program_service.get_program_queryset().filter(
            user=self.request.user,
            is_published=True,
        ))
        feedbacks = list(
            WorkoutProgramFeedback.objects.filter(
                program_id__in=[program.pk for program in programs]
            ).select_related("day")
        )
        feedback_by_day = {
            feedback.day_id: feedback
            for feedback in feedbacks
            if feedback.day_id is not None
        }
        legacy_feedback_by_program = {}
        latest_feedback_by_program = {}
        for feedback in sorted(
            feedbacks,
            key=lambda item: item.submitted_at,
            reverse=True,
        ):
            latest_feedback_by_program.setdefault(feedback.program_id, feedback)
            if feedback.day_id is None:
                legacy_feedback_by_program.setdefault(feedback.program_id, feedback)

        for program in programs:
            program.can_access = _has_workout_program_access(self.request.user, program)
            program.training_cycle = TrainingPeriodizationService.current_week(program)
            program.periodization_plan = (program.generation_metadata or {}).get("periodization") or {}
            corrective_items = list(program.corrective_items.all())
            program.warmup_correctives = [
                item for item in corrective_items if item.phase == "warmup"
            ]
            program.cooldown_correctives = [
                item for item in corrective_items if item.phase == "cooldown"
            ]
            program.user_feedback = latest_feedback_by_program.get(program.pk)
            for day_index, day in enumerate(program.days.all()):
                day.user_feedback = feedback_by_day.get(day.pk)
                day.completion_weeks = program.duration_weeks
                day.completed_count = min(
                    getattr(getattr(day, "progress", None), "completed_count", 0),
                    program.duration_weeks,
                )
                day.completion_checks = range(1, program.duration_weeks + 1)
                if day.user_feedback is None and day_index == 0:
                    day.user_feedback = legacy_feedback_by_program.get(program.pk)
            attach_performance_context(program)
            attach_best_record_context(program)
        context["programs"] = programs
        try:
            requested_program_id = int(self.request.GET.get("open_program", ""))
        except (TypeError, ValueError):
            requested_program_id = None
        context["selected_program_id"] = next(
            (
                program.pk
                for program in programs
                if program.pk == requested_program_id and program.can_access
            ),
            None,
        )
        return context
