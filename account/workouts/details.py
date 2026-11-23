"""Workout PDF and movement detail pages."""

from __future__ import annotations

from django.contrib import messages
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import TemplateView

from account.models import CorrectiveExercise, Exercise, WorkoutProgram, WorkoutProgramPayment
from account.portal_mixins import UserPortalRequiredMixin
from account.services.gym_program_pdf import build_program_pdf
from account.view_mixins import AccountPageMixin

from .support import gym_program_service, _has_workout_program_access


class ProfileWorkoutProgramPdfView(UserPortalRequiredMixin, View):
    def get(self, request, pk):
        program = get_object_or_404(
            gym_program_service.get_program_queryset(),
            pk=pk,
            user=request.user,
            is_published=True,
        )
        if not _has_workout_program_access(request.user, program):
            messages.info(request, _("برای دریافت این برنامه ابتدا هزینه آن را پرداخت کنید."))
            return redirect("register:profile_workout_programs")
        response = HttpResponse(
            build_program_pdf(program, request=request),
            content_type="application/pdf",
        )
        response["Content-Disposition"] = f'attachment; filename="workout-program-{program.pk}.pdf"'
        return response


class ProfileWorkoutMovementView(AccountPageMixin, UserPortalRequiredMixin, TemplateView):
    template_name = "account/workout-movement-detail.html"
    active_section = "workout"
    account_title = _("نمایش حرکت")
    account_description = _("ویدیوی حرکت انتخاب‌شده از کتابخانه ایگوفیت.")

    def get(self, request, *args, **kwargs):
        self.movement_kind = kwargs.get("kind")
        self.movement_id = kwargs.get("movement_id")
        self.return_program = None
        requested_program_id = request.GET.get("program")
        try:
            requested_program_id = int(requested_program_id)
        except (TypeError, ValueError):
            requested_program_id = None

        if self.movement_kind == "exercise":
            movement_queryset = Exercise.objects.select_related(
                "primary_muscle",
                "secondary_muscle",
                "body_part",
            )
            if requested_program_id:
                requested_program = WorkoutProgram.objects.filter(
                    pk=requested_program_id,
                    user=request.user,
                    is_published=True,
                ).first()
                if requested_program and _has_workout_program_access(request.user, requested_program):
                    movement_queryset = movement_queryset.filter(
                        Q(program_items__day__program=requested_program)
                        | Q(superset_program_items__day__program=requested_program)
                        | Q(third_program_items__day__program=requested_program)
                    ).distinct()
                    self.return_program = requested_program

            self.movement = get_object_or_404(
                movement_queryset.filter(
                    (
                        Q(
                            program_items__day__program__user=request.user,
                            program_items__day__program__is_published=True,
                            program_items__day__program__requires_payment=False,
                        )
                        | Q(
                            program_items__day__program__user=request.user,
                            program_items__day__program__is_published=True,
                            program_items__day__program__payments__user=request.user,
                            program_items__day__program__payments__status=WorkoutProgramPayment.Status.PAID,
                        )
                        | Q(
                            superset_program_items__day__program__user=request.user,
                            superset_program_items__day__program__is_published=True,
                            superset_program_items__day__program__requires_payment=False,
                        )
                        | Q(
                            superset_program_items__day__program__user=request.user,
                            superset_program_items__day__program__is_published=True,
                            superset_program_items__day__program__payments__user=request.user,
                            superset_program_items__day__program__payments__status=WorkoutProgramPayment.Status.PAID,
                        )
                        | Q(
                            third_program_items__day__program__user=request.user,
                            third_program_items__day__program__is_published=True,
                            third_program_items__day__program__requires_payment=False,
                        )
                        | Q(
                            third_program_items__day__program__user=request.user,
                            third_program_items__day__program__is_published=True,
                            third_program_items__day__program__payments__user=request.user,
                            third_program_items__day__program__payments__status=WorkoutProgramPayment.Status.PAID,
                        )
                    ),
                ).distinct(),
                pk=self.movement_id,
            )
            if self.return_program is None:
                self.return_program = (
                    WorkoutProgram.objects.filter(
                        user=request.user,
                        is_published=True,
                    ).filter(
                        Q(requires_payment=False)
                        | Q(payments__user=request.user, payments__status=WorkoutProgramPayment.Status.PAID)
                    )
                    .filter(
                        Q(days__items__exercise_id=self.movement.pk)
                        | Q(days__items__superset_exercise_id=self.movement.pk)
                        | Q(days__items__third_exercise_id=self.movement.pk)
                    )
                    .order_by("pk")
                    .first()
                )
        elif self.movement_kind == "corrective":
            movement_queryset = CorrectiveExercise.objects.select_related(
                "equipment",
                "abnormality_type",
            )
            if requested_program_id:
                requested_program = WorkoutProgram.objects.filter(
                    pk=requested_program_id,
                    user=request.user,
                    is_published=True,
                ).first()
                if requested_program and _has_workout_program_access(request.user, requested_program):
                    movement_queryset = movement_queryset.filter(
                        program_items__program=requested_program
                    ).distinct()
                    self.return_program = requested_program

            self.movement = get_object_or_404(
                movement_queryset.filter(
                    Q(
                        program_items__program__user=request.user,
                        program_items__program__is_published=True,
                        program_items__program__requires_payment=False,
                    )
                    | Q(
                        program_items__program__user=request.user,
                        program_items__program__is_published=True,
                        program_items__program__payments__user=request.user,
                        program_items__program__payments__status=WorkoutProgramPayment.Status.PAID,
                    ),
                ).distinct(),
                pk=self.movement_id,
            )
            if self.return_program is None:
                self.return_program = (
                    WorkoutProgram.objects.filter(
                        user=request.user,
                        is_published=True,
                        corrective_items__corrective_exercise_id=self.movement.pk,
                    ).filter(
                        Q(requires_payment=False)
                        | Q(payments__user=request.user, payments__status=WorkoutProgramPayment.Status.PAID)
                    )
                    .order_by("pk")
                    .first()
                )
        else:
            return redirect("register:profile_workout_programs")
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["movement"] = self.movement
        context["movement_kind"] = self.movement_kind
        context["return_program"] = self.return_program
        return context
