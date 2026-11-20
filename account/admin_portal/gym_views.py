from __future__ import annotations

import json
import hashlib
import jdatetime

from django.contrib import messages
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.signing import BadSignature, SignatureExpired
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.http import HttpResponse
from django.http import Http404
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import TemplateView

from account.admin_forms import (
    AutomaticProgrammingForm,
    automatic_programming_user_queryset,
    CardioExerciseForm,
    CorrectiveExerciseForm,
    ExerciseForm,
    LibrarySearchForm,
    MuscleForm,
    MuscleVolumeLandmarkForm,
    JalaliWorkoutProgramForm,
    ProgramTemplateForm,
    WorkoutProgramForm,
    WorkoutProgramPayloadForm,
    build_lookup_form,
)
from account.admin_portal.views import AdminPageMixin, AdminUserMixin, StaffPageMixin, StaffUserMixin
from account.portal_mixins import coach_roster_queryset, is_coach_only
from account.models import (
    CardioExercise,
    CorrectiveExercise,
    Exercise,
    ExerciseAbnormalityType,
    ExerciseBodyPart,
    ExerciseDifficultyLevel,
    ExerciseEquipmentType,
    ExerciseExecutionEquipmentType,
    ExerciseGoal,
    ExerciseJointType,
    ExerciseMovementType,
    ExercisePowerType,
    ExercisePressureType,
    ExerciseRepetitionType,
    ExerciseRestType,
    ExerciseSecondaryMovementType,
    ExerciseSetType,
    ExerciseSportType,
    Muscle,
    MuscleVolumeLandmark,
    ProgramTemplate,
    AthleteTrainingProfile,
    TrainingAdjustmentRecommendation,
    UserHealthRecord,
    WorkoutProgram,
    WorkoutProgramFeedback,
    User,
)
from account.services.gym_library_service import GymLibraryService
from account.services.gym_program_pdf import build_program_pdf
from account.services.gym_program_service import GymProgramService
from account.services.automatic_program_service import AutomaticProgramService
from account.services.exercise_goal_service import ExerciseGoalService
from account.services.training_recommendation_review_service import TrainingRecommendationReviewService
from account.services.program_template_service import ProgramTemplateService
from account.services.training_adherence_service import TrainingAdherenceService
from account.services.notification_service import NotificationService

gym_library_service = GymLibraryService()
gym_program_service = GymProgramService()
automatic_program_service = AutomaticProgramService()

DEFAULT_LOOKUP_FIELDS = ("name", "name_en")
GYM_PAGE_SIZE = 20
GYM_PAGE_SIZES = (10, 15, 20)


def paginate_gym_queryset(request, queryset):
    """Return the requested gym page together with standard pagination context."""
    try:
        page_size = int(request.GET.get("page_size", GYM_PAGE_SIZE))
    except (TypeError, ValueError):
        page_size = GYM_PAGE_SIZE
    if page_size not in GYM_PAGE_SIZES:
        page_size = GYM_PAGE_SIZE
    paginator = Paginator(queryset, page_size)
    page_obj = paginator.get_page(request.GET.get("page"))
    return {
        "paginator": paginator,
        "page_obj": page_obj,
        "is_paginated": page_obj.has_other_pages(),
        "page_size": page_size,
        "page_sizes": GYM_PAGE_SIZES,
    }


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

LOOKUP_REGISTRY = {
    "body-part": {"model": ExerciseBodyPart, "title": _("بخش بدن")},
    "movement-type": {"model": ExerciseMovementType, "title": _("نوع حرکت")},
    "joint-type": {"model": ExerciseJointType, "title": _("نوع مفصل")},
    "power-type": {"model": ExercisePowerType, "title": _("نوع قدرت")},
    "difficulty-level": {"model": ExerciseDifficultyLevel, "title": _("سطح دشواری")},
    "equipment-type": {"model": ExerciseEquipmentType, "title": _("تجهیزات"), "fields": ("name", "name_en", "load_increment_kg")},
    "execution-equipment-type": {"model": ExerciseExecutionEquipmentType, "title": _("نوع تجهیزات اجرایی")},
    "secondary-movement-type": {"model": ExerciseSecondaryMovementType, "title": _("نوع حرکت دوم")},
    "abnormality-type": {"model": ExerciseAbnormalityType, "title": _("ناهنجاری")},
    "pressure-type": {"model": ExercisePressureType, "title": _("نوع فشار")},
    "sport-type": {"model": ExerciseSportType, "title": _("نوع ورزش")},
    "goal": {"model": ExerciseGoal, "title": _("اهداف برنامه"), "fields": ("name", "name_en")},
    "set-type": {"model": ExerciseSetType, "title": _("نوع ست"), "fields": ("set_count", "goal", "method")},
    "repetition-type": {"model": ExerciseRepetitionType, "title": _("نوع تکرار"), "fields": ("reps", "goal", "method")},
    "rest-type": {"model": ExerciseRestType, "title": _("نوع استراحت"), "fields": ("rest_time", "goal", "method")},
}


class GymLibraryView(AdminPageMixin, TemplateView):
    template_name = "admin_portal/gym/library.html"
    active_section = "library"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(gym_library_service.get_dashboard_context())
        return context


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


class CardioExerciseListView(AdminPageMixin, TemplateView):
    template_name = "admin_portal/gym/cardio_exercise_list.html"
    active_section = "library"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        query = (self.request.GET.get("query") or "").strip()
        page = paginate_gym_queryset(
            self.request,
            gym_library_service.search_cardio_exercises(query).order_by("name", "pk"),
        )
        context.update(
            {
                "form": LibrarySearchForm(initial={"query": query}),
                "search_query": query,
                "cardio_exercises": page["page_obj"].object_list,
            }
        )
        context.update(page)
        return context


class CardioExerciseFormView(AdminPageMixin, View):
    template_name = "admin_portal/gym/cardio_exercise_form.html"
    active_section = "library"

    def get(self, request, pk=None):
        instance = get_object_or_404(CardioExercise, pk=pk) if pk else None
        return render(request, self.template_name, self._context(CardioExerciseForm(instance=instance), instance))

    def post(self, request, pk=None):
        instance = get_object_or_404(CardioExercise, pk=pk) if pk else None
        form = CardioExerciseForm(request.POST, request.FILES, instance=instance)
        if form.is_valid():
            gym_library_service.save_cardio_exercise(form)
            messages.success(request, _("حرکت هوازی ذخیره شد."))
            return redirect("register:admin_cardio_exercise_list")
        return render(request, self.template_name, self._context(form, instance))

    def _context(self, form, instance):
        return {
            "form": form,
            "cardio_exercise": instance,
            "active_section": self.active_section,
            "page_title": _("ویرایش حرکت هوازی") if instance else _("افزودن حرکت هوازی"),
        }


def redirect_to_gym_page(request, fallback_route):
    return_to = request.POST.get("return_to", "")
    if return_to and url_has_allowed_host_and_scheme(
        return_to,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(return_to)
    return redirect(fallback_route)


class GymLibraryDeleteView(AdminPageMixin, View):
    model = None
    service_method = ""
    list_route = ""
    success_message = ""

    def post(self, request, pk):
        item = get_object_or_404(self.model, pk=pk)
        try:
            getattr(gym_library_service, self.service_method)(item)
        except ProtectedError:
            messages.error(request, _("این مورد به‌دلیل استفاده در داده‌های دیگر قابل حذف نیست."))
        else:
            messages.success(request, self.success_message)
        return redirect_to_gym_page(request, self.list_route)


class GymLibraryBulkDeleteView(AdminPageMixin, View):
    model = None
    service_method = ""
    list_route = ""

    def post(self, request):
        selected_ids = [value for value in request.POST.getlist("selected_ids") if value.isdecimal()]
        if not selected_ids:
            messages.info(request, _("برای حذف گروهی، ابتدا موردی را انتخاب کنید."))
            return redirect_to_gym_page(request, self.list_route)

        deleted_count = 0
        protected_count = 0
        for item in self.model.objects.filter(pk__in=selected_ids).order_by("pk"):
            try:
                getattr(gym_library_service, self.service_method)(item)
            except ProtectedError:
                protected_count += 1
            else:
                deleted_count += 1

        if deleted_count:
            messages.success(
                request,
                _("%(count)s مورد حذف شد.") % {"count": deleted_count},
            )
        if protected_count:
            messages.error(
                request,
                _("%(count)s مورد به‌دلیل استفاده در داده‌های دیگر حذف نشد.")
                % {"count": protected_count},
            )
        return redirect_to_gym_page(request, self.list_route)


class CardioExerciseDeleteView(GymLibraryDeleteView):
    model = CardioExercise
    service_method = "delete_cardio_exercise"
    list_route = "register:admin_cardio_exercise_list"
    success_message = _("حرکت هوازی حذف شد.")


class CardioExerciseBulkDeleteView(GymLibraryBulkDeleteView):
    model = CardioExercise
    service_method = "delete_cardio_exercise"
    list_route = "register:admin_cardio_exercise_list"


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
            signature = self._settings_signature(
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
                    post_data["applied_settings"] = self._settings_signature(
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
                    preview = self._decode(
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
                    preview_data = self._decode(
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
                    preview = self._decode(
                        request.POST.get("days_json"),
                        request.POST.get("correctives_json"),
                        request.POST.get("generation_metadata_json"),
                        request.POST.get("generation_preview_token"),
                    )
            elif action == "notify_adherence":
                preview = self._decode(
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

    @staticmethod
    def _settings_signature(cleaned_data, *, equipment_ids=None):
        athlete = cleaned_data["user"]
        profile = AthleteTrainingProfile.objects.filter(user=athlete).prefetch_related("available_equipment").first()
        effective_equipment_ids = (
            sorted(profile.available_equipment.values_list("pk", flat=True))
            if equipment_ids is None and profile
            else sorted(equipment_ids or [])
        )
        requested_training_goal = cleaned_data.get("athlete_goal") or None
        selected_training_goal = requested_training_goal or (profile.primary_goal if profile else None)
        effective_goal = cleaned_data["goal"]
        mapped_training_goal = automatic_program_service.program_goal_for_training_goal(selected_training_goal)
        if mapped_training_goal and not automatic_program_service._is_cardio_goal(effective_goal):
            effective_goal = mapped_training_goal
        profile_data = None
        if profile:
            profile_data = {
                "updated_at": profile.updated_at.isoformat(),
                "goal": profile.primary_goal,
                "sessions": profile.sessions_per_week,
                "experience": profile.experience_years,
                "session_duration_minutes": profile.session_duration_minutes,
                "preferred_days": profile.preferred_days,
                "equipment": sorted(profile.available_equipment.values_list("pk", flat=True)),
            }
        readiness = AutomaticProgramService._readiness_snapshot(athlete.pk)
        adherence = TrainingAdherenceService.snapshot(athlete)
        muscle_fatigue = AutomaticProgramService._muscle_fatigue_snapshot(
            athlete.pk,
            profile.experience_years if profile else None,
        )
        preferences = AutomaticProgramService._exercise_preferences(athlete.pk)
        last_performance = AutomaticProgramService._last_performance_snapshot(athlete.pk)
        template = cleaned_data.get("template")
        exercises = list(
            Exercise.objects.prefetch_related("required_equipment_types", "excluded_abnormalities")
            .select_related("difficulty_level", "movement_type", "secondary_movement_type", "equipment_type")
            .order_by("pk")
        )
        cardio_catalog = list(CardioExercise.objects.prefetch_related("required_equipment_types").order_by("pk"))
        payload = {
            "algorithm_version": AutomaticProgramService.ALGORITHM_VERSION,
            "user": athlete.pk,
            "program_type": cleaned_data.get("program_type") or "normal",
            "training_methods": sorted(cleaned_data.get("training_methods") or []),
            "difficulty": cleaned_data["difficulty"].pk,
            "abnormalities": sorted(item.pk for item in cleaned_data.get("abnormalities", [])),
            "sessions": cleaned_data["sessions_per_week"],
            "movements": cleaned_data["movements_per_session"],
            "goal": effective_goal,
            "requested_training_goal": requested_training_goal,
            "selected_training_goal": selected_training_goal,
            "seed": cleaned_data.get("seed") or "",
            "equipment_selection": effective_equipment_ids,
            "selected_targets": sorted(item.pk for item in cleaned_data.get("target_secondary_movement_types", [])),
            "movement_counts": cleaned_data.get("secondary_movement_counts_json") or {},
            "session_targets": cleaned_data.get("session_movement_targets_json") or [],
            "profile": profile_data,
            "readiness": readiness,
            "adherence": adherence,
            "muscle_fatigue": muscle_fatigue,
            "exercise_preferences": {key: sorted(value) for key, value in preferences.items()},
            "last_performance": last_performance,
            "approved_progression": sorted(
                (key, str(item.reviewed_load_kg), item.reviewed_at.isoformat() if item.reviewed_at else "")
                for key, item in AutomaticProgramService._approved_progression_loads(athlete.pk).items()
            ),
            "approved_deloads": sorted(
                (key, item.pk)
                for key, item in AutomaticProgramService._approved_deload_recommendations(athlete.pk).items()
            ),
            "approved_rep_progressions": sorted(
                (key, item.pk)
                for key, item in AutomaticProgramService._approved_rep_progressions(athlete.pk).items()
            ),
            "volume_landmarks": list(
                MuscleVolumeLandmark.objects.order_by("pk").values_list(
                    "pk", "mev", "mav", "mrv", "updated_at"
                )
            ),
            "template": {
                "id": template.pk,
                "updated_at": template.updated_at.isoformat(),
                "payload": template.payload,
                "weeks": template.weeks,
                "training_goal": template.training_goal,
                "difficulty": template.difficulty_id,
                "days_per_week": template.days_per_week,
            } if template else None,
            "exercise_library": AutomaticProgramService._library_fingerprint(exercises),
            "cardio_library": [
                (item.pk, item.name, item.name_en, item.performance_type, sorted(required.pk for required in item.required_equipment_types.all()))
                for item in cardio_catalog
            ],
        }
        normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def _decode(days_json, correctives_json, generation_metadata_json=None, preview_token=None):
        try:
            days = json.loads(days_json or "[]")
            correctives = json.loads(correctives_json or "[]")
            metadata = json.loads(generation_metadata_json or "{}")
            return {
                "days": days if isinstance(days, list) else [],
                "correctives": correctives if isinstance(correctives, list) else [],
                "metadata": metadata if isinstance(metadata, dict) else {},
                "token": str(preview_token or ""),
            }
        except (TypeError, ValueError):
            return {"days": [], "correctives": [], "metadata": {}, "token": ""}

    def _context(self, form, preview=None, *, adherence_notification_sent=False):
        athletes = list(form.fields["user"].queryset)
        athlete_ids = [athlete.pk for athlete in athletes]
        training_profiles = {
            item.user_id: item
            for item in AthleteTrainingProfile.objects.filter(user_id__in=athlete_ids)
            .prefetch_related("available_equipment")
        }
        athlete_abnormalities = {}
        for athlete_id, abnormality_id in UserHealthRecord.objects.filter(
            user_id__in=athlete_ids,
            category=UserHealthRecord.CategoryChoices.ABNORMALITY,
            abnormality_type__isnull=False,
        ).order_by("user_id", "abnormality_type_id").values_list(
            "user_id", "abnormality_type_id"
        ).distinct():
            athlete_abnormalities.setdefault(athlete_id, []).append(abnormality_id)
        selected_athlete = form.cleaned_data.get("user") if getattr(form, "is_bound", False) and form.is_valid() else None
        selected_profile = (
            AthleteTrainingProfile.objects.filter(user=selected_athlete)
            .prefetch_related("available_equipment")
            .first()
            if selected_athlete
            else None
        )
        selected_adherence = TrainingAdherenceService.snapshot(selected_athlete) if selected_athlete else None
        preview_data = preview if isinstance(preview, dict) else {
            "days": [], "correctives": [], "metadata": {}, "token": ""
        }
        preview_adherence = (preview_data.get("metadata") or {}).get("adherence") or {}
        low_adherence = bool(
            (selected_adherence and selected_adherence.get("available")
             and selected_adherence.get("percent") is not None
             and selected_adherence["percent"] < TrainingAdherenceService.LOW_ADHERENCE_PERCENT)
            or (preview_adherence.get("available")
                and preview_adherence.get("percent") is not None
                and preview_adherence["percent"] < TrainingAdherenceService.LOW_ADHERENCE_PERCENT)
        )
        preview_warnings = list(preview_data.get("warnings") or [])
        if low_adherence:
            adherence_warning_label = str(_("پایبندی ۳۰روزه"))
            preview_warnings = [
                warning for warning in preview_warnings
                if adherence_warning_label not in str(warning)
            ]
        selected_muscle_fatigue = (
            AutomaticProgramService._muscle_fatigue_snapshot(
                selected_athlete.pk,
                selected_profile.experience_years if selected_profile else None,
            )
            if selected_athlete
            else {}
        )
        exercise_catalog = []
        cardio_catalog = []
        body_part_catalog = []
        corrective_catalog = []
        if preview_data.get("days") or preview_data.get("correctives"):
            exercise_catalog = [
                {
                    "id": item.pk,
                    "name": item.name,
                    "secondary_movement_type": item.secondary_movement_type.name if item.secondary_movement_type else "",
                    "difficulty_level": item.difficulty_level.name if item.difficulty_level else "",
                    "body_part_id": item.body_part_id,
                    "body_part": item.body_part.name if item.body_part else "",
                    "equipment_ids": sorted(
                        [required.pk for required in item.required_equipment_types.all()]
                        or ([item.equipment_type_id] if item.equipment_type_id else [])
                    ),
                    "excluded_abnormality_ids": sorted(
                        abnormality.pk for abnormality in item.excluded_abnormalities.all()
                    ),
                }
                for item in Exercise.objects.select_related(
                    "body_part", "secondary_movement_type", "difficulty_level"
                ).only(
                    "pk",
                    "name",
                    "secondary_movement_type_id",
                    "secondary_movement_type__name",
                    "difficulty_level_id",
                    "difficulty_level__name",
                    "body_part_id",
                    "body_part__name",
                    "equipment_type_id",
                ).prefetch_related("required_equipment_types", "excluded_abnormalities").all()
            ]
            cardio_catalog = [
                {
                    "id": item.pk,
                    "name": item.name,
                    "performance_type": item.performance_type,
                    "performance_type_label": item.get_performance_type_display(),
                    "equipment_ids": sorted(required.pk for required in item.required_equipment_types.all()),
                }
                for item in CardioExercise.objects.only(
                    "pk", "name", "performance_type"
                ).prefetch_related("required_equipment_types").all()
            ]
            body_part_catalog = [
                {"id": item.pk, "name": item.name}
                for item in ExerciseBodyPart.objects.only("pk", "name")
            ]
            corrective_catalog = [
                {"id": item.pk, "name": item.name}
                for item in CorrectiveExercise.objects.only("pk", "name")
            ]
        return {
            "form": form,
            "preview": preview_data,
            "preview_warnings": preview_warnings,
            "exercise_catalog": exercise_catalog,
            "cardio_catalog": cardio_catalog,
            "body_part_catalog": body_part_catalog,
            "corrective_catalog": corrective_catalog,
            "athlete_catalog": [
                {
                    "id": athlete.pk,
                    "name": " ".join(
                        value.strip()
                        for value in (athlete.first_name, athlete.last_name)
                        if value and value.strip()
                    ) or athlete.fullname,
                    "abnormality_ids": athlete_abnormalities.get(athlete.pk, []),
                    "profile": (
                        {
                            "goal": training_profiles[athlete.pk].get_primary_goal_display(),
                            "primary_goal": training_profiles[athlete.pk].primary_goal,
                            "experience_years": training_profiles[athlete.pk].experience_years,
                            "session_duration_minutes": training_profiles[athlete.pk].session_duration_minutes,
                            "preferred_days": training_profiles[athlete.pk].preferred_days or [],
                            "equipment": [item.name for item in training_profiles[athlete.pk].available_equipment.all()],
                            "equipment_ids": sorted(item.pk for item in training_profiles[athlete.pk].available_equipment.all()),
                        }
                        if athlete.pk in training_profiles
                        else None
                    ),
                }
                for athlete in athletes
            ],
            "selected_profile": selected_profile,
            "selected_athlete": selected_athlete,
            "selected_adherence": selected_adherence,
            "adherence_warning_threshold": TrainingAdherenceService.LOW_ADHERENCE_PERCENT,
            "adherence_notification_sent": adherence_notification_sent,
            "selected_muscle_fatigue": selected_muscle_fatigue,
            "selected_abnormality_ids": sorted(item.pk for item in form.cleaned_data.get("abnormalities", [])) if getattr(form, "is_bound", False) and form.is_valid() else [],
            "jalali_today": jdatetime.date.fromgregorian(
                date=timezone.localdate()
            ).strftime("%Y/%m/%d"),
            "active_section": self.active_section,
            "is_coach_only": is_coach_only(self.request.user),
            "page_title": _("برنامه‌ریزی خودکار"),
            "default_start_date": (
                self.request.POST.get("start_date")
                or jdatetime.date.fromgregorian(date=timezone.localdate()).strftime("%Y/%m/%d")
            ),
            "posted_end_date": self.request.POST.get("end_date", ""),
        }


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


class MuscleListView(AdminPageMixin, TemplateView):
    template_name = "admin_portal/gym/muscle_list.html"
    active_section = "library"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        query = (self.request.GET.get("query") or "").strip()
        muscles_page = paginate_gym_queryset(
            self.request, gym_library_service.search_muscles(query).order_by("name", "pk")
        )
        context.update(
            {
                "form": LibrarySearchForm(initial={"query": query}),
                "search_query": query,
                "muscles": muscles_page["page_obj"].object_list,
            }
        )
        context.update(muscles_page)
        return context


class MuscleFormView(AdminPageMixin, View):
    template_name = "admin_portal/gym/muscle_form.html"
    active_section = "library"

    def get(self, request, pk=None):
        instance = get_object_or_404(Muscle, pk=pk) if pk else None
        form = MuscleForm(instance=instance)
        return render(request, self.template_name, self._context(form, instance))

    def post(self, request, pk=None):
        instance = get_object_or_404(Muscle, pk=pk) if pk else None
        form = MuscleForm(request.POST, request.FILES, instance=instance)
        if form.is_valid():
            gym_library_service.save_muscle(form)
            return redirect("register:admin_muscle_list")
        return render(request, self.template_name, self._context(form, instance))

    def _context(self, form, instance):
        return {
            "form": form,
            "muscle": instance,
            "active_section": self.active_section,
            "page_title": _("ویرایش عضله") if instance else _("افزودن عضله"),
        }


class MuscleDeleteView(GymLibraryDeleteView):
    model = Muscle
    service_method = "delete_muscle"
    list_route = "register:admin_muscle_list"
    success_message = _("عضله حذف شد.")


class MuscleBulkDeleteView(GymLibraryBulkDeleteView):
    model = Muscle
    service_method = "delete_muscle"
    list_route = "register:admin_muscle_list"


class ExerciseListView(AdminPageMixin, TemplateView):
    template_name = "admin_portal/gym/exercise_list.html"
    active_section = "library"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        query = (self.request.GET.get("query") or "").strip()
        exercises_page = paginate_gym_queryset(
            self.request, gym_library_service.search_exercises(query).order_by("name", "pk")
        )
        context.update(
            {
                "form": LibrarySearchForm(initial={"query": query}),
                "search_query": query,
                "exercises": exercises_page["page_obj"].object_list,
            }
        )
        context.update(exercises_page)
        return context


class ExerciseFormView(AdminPageMixin, View):
    template_name = "admin_portal/gym/exercise_form.html"
    active_section = "library"

    def get(self, request, pk=None):
        instance = get_object_or_404(Exercise, pk=pk) if pk else None
        form = ExerciseForm(instance=instance)
        return render(request, self.template_name, self._context(form, instance))

    def post(self, request, pk=None):
        instance = get_object_or_404(Exercise, pk=pk) if pk else None
        form = ExerciseForm(request.POST, request.FILES, instance=instance)
        if form.is_valid():
            gym_library_service.save_exercise(form)
            return redirect("register:admin_exercise_list")
        return render(request, self.template_name, self._context(form, instance))

    def _context(self, form, instance):
        return {
            "form": form,
            "exercise": instance,
            "active_section": self.active_section,
            "page_title": _("ویرایش حرکت") if instance else _("افزودن حرکت"),
        }


class ExerciseDeleteView(GymLibraryDeleteView):
    model = Exercise
    service_method = "delete_exercise"
    list_route = "register:admin_exercise_list"
    success_message = _("حرکت تمرینی حذف شد.")


class ExerciseBulkDeleteView(GymLibraryBulkDeleteView):
    model = Exercise
    service_method = "delete_exercise"
    list_route = "register:admin_exercise_list"


class CorrectiveExerciseListView(AdminPageMixin, TemplateView):
    template_name = "admin_portal/gym/corrective_list.html"
    active_section = "library"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        query = (self.request.GET.get("query") or "").strip()
        correctives_page = paginate_gym_queryset(
            self.request, gym_library_service.search_correctives(query).order_by("name", "pk")
        )
        context.update(
            {
                "form": LibrarySearchForm(initial={"query": query}),
                "search_query": query,
                "correctives": correctives_page["page_obj"].object_list,
            }
        )
        context.update(correctives_page)
        return context


class CorrectiveExerciseFormView(AdminPageMixin, View):
    template_name = "admin_portal/gym/corrective_form.html"
    active_section = "library"

    def get(self, request, pk=None):
        instance = get_object_or_404(CorrectiveExercise, pk=pk) if pk else None
        form = CorrectiveExerciseForm(instance=instance)
        return render(request, self.template_name, self._context(form, instance))

    def post(self, request, pk=None):
        instance = get_object_or_404(CorrectiveExercise, pk=pk) if pk else None
        form = CorrectiveExerciseForm(request.POST, request.FILES, instance=instance)
        if form.is_valid():
            gym_library_service.save_corrective(form)
            return redirect("register:admin_corrective_list")
        return render(request, self.template_name, self._context(form, instance))

    def _context(self, form, instance):
        return {
            "form": form,
            "corrective": instance,
            "active_section": self.active_section,
            "page_title": _("ویرایش حرکت اصلاحی") if instance else _("افزودن حرکت اصلاحی"),
        }


class CorrectiveExerciseDeleteView(GymLibraryDeleteView):
    model = CorrectiveExercise
    service_method = "delete_corrective"
    list_route = "register:admin_corrective_list"
    success_message = _("حرکت اصلاحی حذف شد.")


class CorrectiveExerciseBulkDeleteView(GymLibraryBulkDeleteView):
    model = CorrectiveExercise
    service_method = "delete_corrective"
    list_route = "register:admin_corrective_list"


class LookupKeyMixin(AdminPageMixin):
    active_section = "library"

    def dispatch(self, request, *args, **kwargs):
        self.lookup_key = kwargs["key"]
        self.lookup_cfg = LOOKUP_REGISTRY.get(self.lookup_key)
        if not self.lookup_cfg:
            raise Http404()
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["lookup_key"] = self.lookup_key
        context["lookup_title"] = self.lookup_cfg["title"]
        return context

    @property
    def lookup_fields(self):
        return self.lookup_cfg.get("fields", DEFAULT_LOOKUP_FIELDS)

    def redirect_to_lookup_list(self, request):
        return_to = request.POST.get("return_to", "")
        if return_to and url_has_allowed_host_and_scheme(
            return_to,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return redirect(return_to)
        return redirect("register:admin_lookup_list", key=self.lookup_key)


class LookupListView(LookupKeyMixin, TemplateView):
    template_name = "admin_portal/gym/lookup_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        fields = self.lookup_fields
        items_page = paginate_gym_queryset(
            self.request,
            gym_library_service.list_lookup(self.lookup_cfg["model"]).order_by("pk"),
        )
        context["items"] = [
            {
                "pk": obj.pk,
                "primary": getattr(obj, fields[0]),
                "secondary": getattr(obj, fields[1]) if len(fields) > 1 else "",
                "tertiary": (
                    obj.get_method_display()
                    if len(fields) > 2 and fields[2] == "method"
                    else getattr(obj, fields[2]) if len(fields) > 2 else ""
                ),
            }
            for obj in items_page["page_obj"].object_list
        ]
        context.update(items_page)
        context["page_title"] = self.lookup_cfg["title"]
        return context


class LookupFormView(LookupKeyMixin, View):
    template_name = "admin_portal/gym/lookup_form.html"

    def get(self, request, key, pk=None):
        instance = get_object_or_404(self.lookup_cfg["model"], pk=pk) if pk else None
        form = build_lookup_form(self.lookup_cfg["model"], fields=self.lookup_fields, instance=instance)
        return render(request, self.template_name, self._context(form, instance))

    def post(self, request, key, pk=None):
        instance = get_object_or_404(self.lookup_cfg["model"], pk=pk) if pk else None
        form = build_lookup_form(self.lookup_cfg["model"], fields=self.lookup_fields, data=request.POST, instance=instance)
        if form.is_valid():
            gym_library_service.save_lookup(form)
            return redirect("register:admin_lookup_list", key=self.lookup_key)
        return render(request, self.template_name, self._context(form, instance))

    def _context(self, form, instance):
        return {
            "form": form,
            "item": instance,
            "lookup_key": self.lookup_key,
            "lookup_title": self.lookup_cfg["title"],
            "active_section": self.active_section,
            "page_title": _("ویرایش %(title)s") % {"title": self.lookup_cfg["title"]} if instance else _("افزودن %(title)s") % {"title": self.lookup_cfg["title"]},
        }


class LookupDeleteView(LookupKeyMixin, View):
    def post(self, request, key, pk):
        success, error_message = gym_library_service.delete_lookup(self.lookup_cfg["model"], pk)
        if success:
            messages.success(request, _("مورد انتخاب‌شده حذف شد."))
        else:
            messages.error(request, error_message)
        return self.redirect_to_lookup_list(request)


class LookupBulkDeleteView(LookupKeyMixin, View):
    def post(self, request, key):
        selected_ids = list(dict.fromkeys(
            value for value in request.POST.getlist("selected_ids") if value.isdecimal()
        ))
        if not selected_ids:
            messages.info(request, _("برای حذف گروهی، ابتدا موردی را انتخاب کنید."))
            return self.redirect_to_lookup_list(request)

        deleted_count = 0
        errors = []
        queryset = self.lookup_cfg["model"].objects.filter(pk__in=selected_ids).order_by("pk")
        for item in queryset:
            success, error_message = gym_library_service.delete_lookup(
                self.lookup_cfg["model"], item.pk
            )
            if success:
                deleted_count += 1
            elif error_message and error_message not in errors:
                errors.append(error_message)

        if deleted_count:
            messages.success(
                request,
                _("%(count)s مورد حذف شد.") % {"count": deleted_count},
            )
        for error_message in errors:
            messages.error(request, error_message)
        return self.redirect_to_lookup_list(request)
