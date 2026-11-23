"""Staff pages for editing movements, muscles, and related lookup data."""

from __future__ import annotations

from django.contrib import messages
from django.db.models.deletion import ProtectedError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import TemplateView

from account.admin_forms import (
    CardioExerciseForm,
    CorrectiveExerciseForm,
    ExerciseForm,
    LibrarySearchForm,
    MuscleForm,
    build_lookup_form,
)
from account.admin_portal.staff.mixins import AdminPageMixin
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
)

from .support import gym_library_service, paginate_gym_queryset, redirect_to_gym_page


DEFAULT_LOOKUP_FIELDS = ("name", "name_en")


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
