"""Staff interface for generating and publishing automatic workout plans."""

from __future__ import annotations

import json

import jdatetime
from django.contrib import messages
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.signing import BadSignature, SignatureExpired
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views import View

from account.admin_forms import (
    AutomaticProgrammingForm,
    automatic_programming_user_queryset,
    JalaliWorkoutProgramForm,
    WorkoutProgramPayloadForm,
)
from account.admin_portal.staff.mixins import StaffPageMixin
from account.portal_mixins import coach_roster_queryset, is_coach_only
from account.models import AthleteTrainingProfile, WorkoutProgram, User
from account.services.automatic_program_service import AutomaticProgramService
from account.services.training_recommendation_review_service import (
    TrainingRecommendationReviewService,
)
from account.services.program_template_service import ProgramTemplateService
from account.services.training_adherence_service import TrainingAdherenceService
from account.services.notification_service import NotificationService

from .support import gym_program_service
from .automatic_context import build_automatic_context
from .automatic_preview import decode_preview, settings_signature


automatic_program_service = AutomaticProgramService()


class AutomaticProgrammingView(StaffPageMixin, View):
    """Generate an editable program payload and optionally publish it to a user."""

    template_name = "admin_portal/gym/automatic_programming.html"
    active_section = "automatic"

    def dispatch(self, request, *args, **kwargs):
        if (
            request.user.is_authenticated
            and is_coach_only(request.user)
            and not request.user.can_use_automatic_programming
        ):
            return redirect("register:admin_search")
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        return render(request, self.template_name, self._context(self._form()))

    def post(self, request):
        post_data = request.POST.copy()
        try:
            athlete_id = int(post_data.get("user", ""))
        except (TypeError, ValueError):
            athlete_id = None
        athletes = automatic_programming_user_queryset().filter(pk=athlete_id) if athlete_id else User.objects.none()
        if is_coach_only(request.user):
            athletes = athletes.filter(coach=request.user, is_active=True)
        athlete = athletes.first()
        if post_data.get("template") and not post_data.get("template_initialized"):
            template = ProgramTemplateService.accessible_to(request.user).filter(pk=post_data.get("template")).first()
            if template:
                template_values = ProgramTemplateService.generation_payload(template)
                post_data["sessions_per_week"] = str(template.days_per_week)
                post_data["difficulty"] = str(template.difficulty_id)
                if template_values["session_movement_targets"]:
                    post_data["session_movement_targets_json"] = json.dumps(
                        template_values["session_movement_targets"], ensure_ascii=False
                    )
                if template_values["secondary_movement_counts"]:
                    post_data["secondary_movement_counts_json"] = json.dumps(
                        template_values["secondary_movement_counts"], ensure_ascii=False
                    )
                target_ids = template_values["target_secondary_movement_type_ids"] or [
                    entry.get("movement_type")
                    for target in template_values["session_movement_targets"]
                    if isinstance(target, dict)
                    for entry in target.get("targets", [])
                    if isinstance(entry, dict) and entry.get("movement_type")
                ]
                if target_ids:
                    post_data.setlist("target_secondary_movement_types", [str(value) for value in target_ids])
                post_data["template_initialized"] = "1"
        if athlete and not post_data.get("title"):
            post_data["title"] = self._program_title(athlete)
        if (
            request.POST.get("action") == "save"
            and "is_published" not in post_data
            and not post_data.get("automatic_program_form_version")
        ):
            # Keep legacy form clients on the field's published-by-default
            # behavior while honoring an unchecked checkbox in the current UI.
            post_data["is_published"] = "on"
        form = self._form(post_data)
        action = request.POST.get("action", "generate")
        preview = None
        adherence_notification_sent = False
        if form.is_valid():
            selected_equipment_ids = (
                [item.pk for item in form.cleaned_data.get("equipment_selection", [])]
                if request.POST.get("equipment_selection_present")
                else None
            )
            signature = settings_signature(
                form.cleaned_data,
                equipment_ids=selected_equipment_ids,
            )
            if action == "apply":
                if form.cleaned_data.get("goal") == "cardio":
                    sessions = form.cleaned_data["sessions_per_week"]
                    targets = [
                        {"name": _("روز %(number)s") % {"number": index}}
                        for index in range(1, sessions + 1)
                    ]
                    selected_ids = []
                else:
                    try:
                        targets, selected_ids = automatic_program_service.build_session_targets(
                            program_type=form.cleaned_data.get("program_type") or WorkoutProgram.ProgramType.NORMAL,
                            sessions=form.cleaned_data["sessions_per_week"],
                            current_targets=form.cleaned_data.get("session_movement_targets_json") or [],
                            selected_ids=[
                                item.pk
                                for item in form.cleaned_data.get("target_secondary_movement_types", [])
                            ],
                        )
                    except ValidationError as exc:
                        form.add_error(None, exc)
                        return render(request, self.template_name, self._context(form, preview))
                post_data.setlist(
                    "session_movement_targets_json",
                    [json.dumps(targets, ensure_ascii=False)],
                )
                post_data.setlist("target_secondary_movement_types", selected_ids)
                form = self._form(post_data)
                if form.is_valid():
                    post_data["applied_settings"] = settings_signature(
                        form.cleaned_data,
                        equipment_ids=selected_equipment_ids,
                    )
                    form = self._form(post_data)
                    form.is_valid()
                messages.info(request, _("چیدمان جلسه‌ها اعمال شد؛ در صورت نیاز آن را ویرایش و سپس برنامه را بسازید."))
                return render(request, self.template_name, self._context(form, preview))

            if action == "save":
                applied_settings = form.cleaned_data.get("applied_settings")
                if applied_settings and applied_settings != signature:
                    form.add_error(None, _("ابتدا تغییرات را اعمال و برنامه را دوباره تولید کنید."))
                    preview = decode_preview(
                        request.POST.get("days_json"),
                        request.POST.get("correctives_json"),
                        request.POST.get("generation_metadata_json"),
                        request.POST.get("generation_preview_token"),
                    )
                    return render(request, self.template_name, self._context(form, preview))
                payload_form = WorkoutProgramPayloadForm(request.POST)
                program_data = post_data.copy()
                if not program_data.get("start_date"):
                    program_data["start_date"] = timezone.localdate().isoformat()
                if is_coach_only(request.user):
                    program_data["is_published"] = "on"
                    program_data["requires_payment"] = ""
                    program_data["price"] = "0"
                program_form = JalaliWorkoutProgramForm(program_data)
                if payload_form.is_valid() and program_form.is_valid():
                    preview_data = decode_preview(
                        request.POST.get("days_json"),
                        request.POST.get("correctives_json"),
                        request.POST.get("generation_metadata_json"),
                        request.POST.get("generation_preview_token"),
                    )
                    generation_metadata = preview_data.get("metadata") or {}
                    if generation_metadata.get("algorithm_version"):
                        try:
                            signed_preview = signing.loads(
                                preview_data.get("token") or "",
                                salt="automatic-program-preview",
                                max_age=7200,
                            )
                        except (BadSignature, SignatureExpired):
                            signed_preview = None
                        if (
                            not signed_preview
                            or signed_preview.get("metadata") != generation_metadata
                            or signed_preview.get("settings_signature") != signature
                        ):
                            form.add_error(None, _("پیش‌نمایش معتبر یا تازه نیست؛ برنامه را دوباره تولید کنید."))
                            return render(request, self.template_name, self._context(form, preview_data))
                    try:
                        recommendation_ids = generation_metadata.get("progression_recommendation_ids") or []
                        if not isinstance(recommendation_ids, list):
                            raise ValidationError(_("اطلاعات پیشنهادهای پیشرفت در پیش‌نمایش معتبر نیست؛ برنامه را دوباره تولید کنید."))
                        with transaction.atomic():
                            locked_recommendations = TrainingRecommendationReviewService.lock_applicable_recommendations(
                                athlete=form.cleaned_data["user"],
                                recommendation_ids=recommendation_ids,
                                program=program_form.instance,
                            )
                            program = gym_program_service.save_program(
                                program_form=program_form,
                                target_user=form.cleaned_data["user"],
                                prescribed_by=request.user,
                                days_payload=payload_form.cleaned_data["days_json"],
                                correctives_payload=payload_form.cleaned_data.get("correctives_json") or [],
                                generation_metadata=generation_metadata or None,
                            )
                            linked_count = TrainingRecommendationReviewService.mark_applied(
                                recommendations=locked_recommendations,
                                program=program,
                            )
                            from account.services.exercise_preference_service import ExercisePreferenceService

                            ExercisePreferenceService.record_program_edits(
                                athlete=program.user,
                                actor=request.user,
                                days_payload=payload_form.cleaned_data["days_json"],
                            )
                    except ValidationError as exc:
                        form.add_error(None, exc)
                    else:
                        message = (
                            _("برنامه خودکار برای کاربر منتشر شد.")
                            if program.is_published
                            else _("برنامه به‌صورت پیش‌نویس ذخیره شد و برای کاربر نمایش داده نمی‌شود.")
                        )
                        if linked_count:
                            message = _("%(count)s پیشنهاد تأییدشده در برنامه ثبت و مصرف شد.") % {"count": linked_count}
                        messages.success(request, message)
                        return redirect("register:admin_program_list", user_id=program.user_id)
                else:
                    for error in list(payload_form.errors.values()) + list(program_form.errors.values()):
                        form.add_error(None, error)
                    preview = decode_preview(
                        request.POST.get("days_json"),
                        request.POST.get("correctives_json"),
                        request.POST.get("generation_metadata_json"),
                        request.POST.get("generation_preview_token"),
                    )
            elif action == "notify_adherence":
                preview = decode_preview(
                    request.POST.get("days_json"),
                    request.POST.get("correctives_json"),
                    request.POST.get("generation_metadata_json"),
                    request.POST.get("generation_preview_token"),
                )
                adherence = TrainingAdherenceService.snapshot(athlete) if athlete else {}
                message = (request.POST.get("adherence_notification_message") or "").strip()
                if not athlete:
                    messages.error(request, _("ورزشکار انتخاب‌شده معتبر نیست؛ اعلان ساخته نشد."))
                elif not adherence.get("available") or adherence.get("percent") is None:
                    messages.error(request, _("اطلاعات پایبندی ۳۰روزهٔ این کاربر در دسترس نیست؛ اعلان ساخته نشد."))
                elif adherence["percent"] >= TrainingAdherenceService.LOW_ADHERENCE_PERCENT:
                    messages.info(request, _("پایبندی کاربر دیگر کمتر از ۵۰٪ نیست؛ اعلان ساخته نشد."))
                elif not message:
                    messages.error(request, _("متن اعلان را وارد کنید."))
                else:
                    NotificationService().create_in_app_notification(
                        athlete,
                        title=str(_("یادآوری برنامهٔ تمرینی")),
                        message=message,
                    )
                    adherence_notification_sent = True
                    messages.success(request, _("اعلان پایبندی برای کاربر انتخاب‌شده ایجاد شد."))
            elif action == "generate":
                movement_type_counts = (
                    form.cleaned_data.get("secondary_movement_counts_json") or {}
                )
                session_movement_targets = (
                    form.cleaned_data.get("session_movement_targets_json") or []
                )
                selected = {
                    str(item.pk): movement_type_counts.get(str(item.pk), 1)
                    for item in form.cleaned_data["target_secondary_movement_types"]
                }
                try:
                    athlete_profile = AthleteTrainingProfile.objects.filter(
                        user=form.cleaned_data["user"]
                    ).first()
                    requested_training_goal = form.cleaned_data.get("athlete_goal") or None
                    selected_training_goal = requested_training_goal or (
                        form.cleaned_data["template"].training_goal
                        if form.cleaned_data.get("template")
                        else athlete_profile.primary_goal if athlete_profile else None
                    )
                    training_goal_source = (
                        "admin" if requested_training_goal
                        else "template" if form.cleaned_data.get("template")
                        else "athlete_profile" if selected_training_goal
                        else "program_goal"
                    )
                    preview = automatic_program_service.generate(
                        difficulty=form.cleaned_data["difficulty"],
                        gender=None,
                        abnormalities=form.cleaned_data.get("abnormalities"),
                        sessions=form.cleaned_data["sessions_per_week"],
                        movements=form.cleaned_data["movements_per_session"],
                        goal=form.cleaned_data["goal"],
                        training_methods=form.cleaned_data.get("training_methods"),
                        secondary_movement_counts=selected,
                        session_movement_targets=session_movement_targets,
                        athlete=form.cleaned_data["user"],
                        training_goal=selected_training_goal,
                        training_goal_source=training_goal_source,
                        seed=form.cleaned_data.get("seed"),
                        template=form.cleaned_data.get("template"),
                        equipment_ids=selected_equipment_ids,
                    )
                except ValidationError as exc:
                    form.add_error(None, exc)
                else:
                    post_data["applied_settings"] = signature
                    form = self._form(post_data)
                    form.is_valid()
                    preview["token"] = signing.dumps(
                        {"metadata": preview.get("metadata") or {}, "settings_signature": signature},
                        salt="automatic-program-preview",
                    )
                    messages.info(request, _("پیش‌نمایش ساخته شد؛ قبل از ذخیره، حرکت‌ها و نسخه‌ها را بررسی کنید."))
        return render(
            request,
            self.template_name,
            self._context(
                form,
                preview,
                adherence_notification_sent=adherence_notification_sent,
            ),
        )

    @staticmethod
    def _program_title(athlete):
        name = " ".join(
            value.strip()
            for value in (athlete.first_name, athlete.last_name)
            if value and value.strip()
        ) or athlete.fullname
        jalali_today = jdatetime.date.fromgregorian(date=timezone.localdate())
        return f"{name} - {jalali_today.strftime('%Y/%m/%d')}"

    def _form(self, data=None):
        form = AutomaticProgrammingForm(data, actor=self.request.user)
        if is_coach_only(self.request.user):
            form.fields["user"].queryset = coach_roster_queryset(self.request.user).order_by("fullname")
        return form

    def _context(self, form, preview=None, *, adherence_notification_sent=False):
        return build_automatic_context(
            self.request, form, preview, active_section=self.active_section,
            adherence_notification_sent=adherence_notification_sent,
        )
