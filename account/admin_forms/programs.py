"""Program editing, templates, and automatic generation forms."""

from django import forms
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from account.froms import INPUT_CLASS
from account.models import (
    ExerciseMovementType,
    ExerciseSecondaryMovementType,
    User,
    WorkoutProgram,
    ExerciseAbnormalityType,
    ExerciseDifficultyLevel,
    ExerciseEquipmentType,
    ExerciseTrainingMethod,
    ProgramTemplate,
)
from account.services.exercise_goal_service import ExerciseGoalService

from .fields import JalaliDateField, _jalali_date_widget


class WorkoutProgramForm(forms.ModelForm):
    start_date = JalaliDateField(
        label=_("تاریخ شروع"),
        initial=timezone.localdate,
        help_text=_("تاریخ شمسی را با قالب سال/ماه/روز وارد کنید؛ در پایگاه داده به‌صورت تاریخ استاندارد ذخیره می‌شود."),
        widget=_jalali_date_widget(),
    )
    end_date = JalaliDateField(
        required=False,
        label=_("تاریخ پایان"),
        help_text=_("اختیاری؛ نباید قبل از تاریخ شروع باشد."),
        widget=_jalali_date_widget(),
    )

    class Meta:
        model = WorkoutProgram
        fields = (
            "title",
            "program_type",
            "start_date",
            "end_date",
            "is_published",
            "requires_payment",
            "price",
            "notes",
            "supplements_note",
            "warmup_notes",
            "cooldown_notes",
        )
        widgets = {
            "title": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": _("مثلاً: برنامه حجم ماه اول")}),
            "program_type": forms.Select(attrs={"class": INPUT_CLASS}),
            "is_published": forms.CheckboxInput(attrs={"class": "admin-checkbox"}),
            "requires_payment": forms.CheckboxInput(attrs={"class": "admin-checkbox", "data-payment-toggle": "true"}),
            "price": forms.NumberInput(
                attrs={"class": INPUT_CLASS, "min": 0, "step": 1, "inputmode": "numeric", "dir": "ltr", "data-payment-price": "true"}
            ),
            "notes": forms.Textarea(attrs={"class": INPUT_CLASS, "rows": 3, "placeholder": _("توضیحات کلی برنامه")}),
            "supplements_note": forms.Textarea(attrs={"class": INPUT_CLASS, "rows": 3, "placeholder": _("مکمل و نکات تغذیه‌ای")}),
            "warmup_notes": forms.Textarea(attrs={"class": INPUT_CLASS, "rows": 3, "placeholder": _("توضیحات گرم کردن")}),
            "cooldown_notes": forms.Textarea(attrs={"class": INPUT_CLASS, "rows": 3, "placeholder": _("توضیحات سرد کردن")}),
        }

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("program_type"):
            cleaned["program_type"] = WorkoutProgram.ProgramType.NORMAL
        if cleaned.get("requires_payment") and not cleaned.get("price"):
            self.add_error("price", _("برای برنامه پولی مبلغ را وارد کنید."))
        if not cleaned.get("requires_payment"):
            cleaned["price"] = 0
        return cleaned

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["price"].required = False


class JalaliWorkoutProgramForm(WorkoutProgramForm):
    """Use Solar Hijri inputs in the admin auto-programming workflow."""

    start_date = JalaliDateField(
        label=_("تاریخ شروع"),
        initial=timezone.localdate,
        help_text=_("تاریخ شمسی را با قالب سال/ماه/روز وارد کنید؛ در پایگاه داده به‌صورت تاریخ استاندارد ذخیره می‌شود."),
        widget=_jalali_date_widget(),
    )
    end_date = JalaliDateField(
        required=False,
        label=_("تاریخ پایان"),
        help_text=_("اختیاری؛ نباید قبل از تاریخ شروع باشد."),
        widget=_jalali_date_widget(),
    )


class WorkoutProgramPayloadForm(forms.Form):
    days_json = forms.JSONField(
        required=True,
        widget=forms.HiddenInput(),
        label=_("روزهای برنامه"),
    )
    correctives_json = forms.JSONField(
        required=False,
        initial=[],
        widget=forms.HiddenInput(),
        label=_("حرکت‌های اصلاحی"),
    )
    generation_metadata_json = forms.JSONField(
        required=False,
        initial={},
        widget=forms.HiddenInput(),
        label=_("مبنای تولید برنامه"),
    )

    def clean_days_json(self):
        value = self.cleaned_data["days_json"]
        if not isinstance(value, list):
            raise forms.ValidationError(_("ساختار روزهای برنامه معتبر نیست."))
        return value


def automatic_programming_user_queryset():
    """Return athletes and coach accounts assigned to an administrator."""
    return User.objects.filter(
        Q(is_admin=False, is_coach=False)
        | Q(is_admin=False, is_coach=True, coach__is_admin=True)
    ).order_by("fullname")


class AutomaticProgrammingForm(forms.Form):
    """Inputs used by the automatic workout designer."""

    user = forms.ModelChoiceField(
        queryset=automatic_programming_user_queryset(),
        label=_("ورزشکار"),
        widget=forms.Select(attrs={"class": INPUT_CLASS}),
    )
    equipment_selection = forms.ModelMultipleChoiceField(
        queryset=ExerciseEquipmentType.objects.order_by("name"),
        required=False,
        label=_("تجهیزات در دسترس برای این برنامه"),
        help_text=_("فقط حرکت‌هایی پیشنهاد می‌شوند که تجهیزات موردنیازشان در این فهرست انتخاب شده باشد. برای تمرین بدون وسیله، «وزن بدن» را انتخاب کنید."),
        widget=forms.CheckboxSelectMultiple(attrs={"class": "automatic-equipment-checkbox"}),
    )
    template = forms.ModelChoiceField(
        queryset=ProgramTemplate.objects.none(),
        required=False,
        label=_("قالب شروع برنامه"),
        help_text=_("قالب فقط تقسیم جلسه‌ها، هدف، سطح و چرخه را وارد می‌کند؛ انتخاب نهایی حرکت‌ها با داده‌های همین ورزشکار فیلتر می‌شود."),
        widget=forms.Select(attrs={"class": INPUT_CLASS, "data-program-template": "true"}),
    )
    template_initialized = forms.CharField(required=False, widget=forms.HiddenInput())
    athlete_goal = forms.ChoiceField(
        choices=(),
        required=False,
        label=_("هدف اصلی ورزشکار"),
        help_text=_(
            "با انتخاب هدف، تنظیمات تولید بر همان اساس انجام می‌شود. در حالت خالی، هدف ثبت‌شدهٔ پروفایل ملاک است؛ "
            "اگر پروفایل موجود نباشد، هدف برنامه استفاده می‌شود. این انتخاب فقط برای همین برنامه است و پروفایل ورزشکار را تغییر نمی‌دهد؛ "
            "در حالت انتخاب برنامهٔ هوازی، نوع هوازی حفظ می‌شود."
        ),
        widget=forms.Select(attrs={"class": INPUT_CLASS, "data-athlete-goal": "true"}),
    )
    program_type = forms.ChoiceField(
        choices=WorkoutProgram.ProgramType.choices,
        required=False,
        initial=WorkoutProgram.ProgramType.NORMAL,
        label=_("نوع برنامه"),
        widget=forms.Select(attrs={"class": INPUT_CLASS}),
    )
    training_methods = forms.MultipleChoiceField(
        choices=tuple(
            choice
            for choice in ExerciseTrainingMethod.choices
            if choice[0] != ExerciseTrainingMethod.NORMAL
        ),
        required=False,
        label=_("روش‌های تمرین"),
        help_text=_(
            "در هر جلسه ۱ یا ۲ حرکت از روش‌های انتخاب‌شده می‌گیرند؛ روش‌ها باید برای هدف برنامه "
            "در کاتالوگ ست، تکرار و استراحت نسخه داشته باشند."
        ),
        widget=forms.CheckboxSelectMultiple(attrs={"class": "automatic-method-checkbox"}),
    )
    difficulty = forms.ModelChoiceField(
        queryset=ExerciseDifficultyLevel.objects.all(),
        label=_("سطح دشواری"),
        widget=forms.Select(attrs={"class": INPUT_CLASS}),
    )
    seed = forms.CharField(
        required=False,
        max_length=64,
        label=_("بذر بازتولید پیش‌نمایش"),
        help_text=_("برای بازسازی همان انتخاب‌ها، بذر نمایش‌داده‌شده در پیش‌نمایش را وارد کنید."),
        widget=forms.TextInput(attrs={"class": INPUT_CLASS, "dir": "ltr", "autocomplete": "off"}),
    )
    abnormalities = forms.ModelMultipleChoiceField(
        queryset=ExerciseAbnormalityType.objects.all(),
        required=False,
        label=_("ناهنجاری‌های اسکلتی"),
        widget=forms.SelectMultiple(attrs={"class": INPUT_CLASS, "size": 5}),
    )
    sessions_per_week = forms.IntegerField(
        min_value=1, max_value=7, initial=3, label=_("جلسه در هفته"),
        widget=forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 1, "max": 7, "dir": "ltr"}),
    )
    movements_per_session = forms.IntegerField(
        min_value=1, max_value=50, initial=6, label=_("حرکت در هر جلسه"),
        widget=forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 1, "max": 50, "dir": "ltr"}),
    )
    goal = forms.ChoiceField(
        choices=(), initial="general", label=_("هدف برنامه"),
        help_text=_(
            "اگر هدف اصلی ورزشکار خالی باشد، هدف برنامه مبنا است. در برنامهٔ هوازی، نوع هوازی انتخاب‌شده ملاک می‌ماند."
        ),
        widget=forms.Select(attrs={"class": INPUT_CLASS}),
    )
    target_secondary_movement_types = forms.ModelMultipleChoiceField(
        queryset=ExerciseSecondaryMovementType.objects.all(),
        required=False,
        label=_("انواع حرکت دوم هدف"),
        widget=forms.SelectMultiple(
            attrs={"class": INPUT_CLASS, "size": 7, "data-secondary-movement-picker": "true"}
        ),
    )
    secondary_movement_counts_json = forms.JSONField(
        required=False, initial={}, widget=forms.HiddenInput()
    )
    session_movement_targets_json = forms.JSONField(
        required=False, initial=[], widget=forms.HiddenInput()
    )
    applied_settings = forms.CharField(required=False, widget=forms.HiddenInput())
    title = forms.CharField(
        max_length=150, initial="",
        label=_("عنوان برنامه"),
        widget=forms.TextInput(attrs={"class": INPUT_CLASS, "readonly": "readonly"}),
    )
    is_published = forms.BooleanField(
        required=False, initial=True, label=_("نمایش برای کاربر"),
        widget=forms.CheckboxInput(attrs={"class": "admin-checkbox"}),
    )
    requires_payment = forms.BooleanField(
        required=False,
        label=_("نیازمند پرداخت"),
        widget=forms.CheckboxInput(attrs={"class": "admin-checkbox", "data-payment-toggle": "true"}),
    )
    price = forms.IntegerField(
        required=False,
        min_value=0,
        label=_("هزینه برنامه (تومان)"),
        widget=forms.NumberInput(
            attrs={"class": INPUT_CLASS, "min": 0, "step": 1, "inputmode": "numeric", "dir": "ltr", "data-payment-price": "true"}
        ),
    )

    def clean_secondary_movement_counts_json(self):
        value = self.cleaned_data.get("secondary_movement_counts_json") or {}
        if not isinstance(value, dict):
            raise forms.ValidationError(_("تنظیمات تعداد حرکت انواع حرکت دوم معتبر نیست."))
        cleaned = {}
        for movement_type_id, count in value.items():
            try:
                movement_type_id = str(int(movement_type_id))
                count = int(count)
            except (TypeError, ValueError):
                raise forms.ValidationError(_("شناسه یا تعداد حرکت معتبر نیست."))
            if int(movement_type_id) <= 0 or count < 0 or count > 20:
                raise forms.ValidationError(_("تعداد حرکت باید بین صفر و بیست باشد."))
            cleaned[movement_type_id] = count
        return cleaned

    def clean(self):
        cleaned = super().clean()
        program_type = cleaned.get("program_type")
        sessions = cleaned.get("sessions_per_week")
        if program_type == WorkoutProgram.ProgramType.SPLIT and sessions not in {3, 4, 5, 6}:
            self.add_error("sessions_per_week", _("Split فقط برای ۳ تا ۶ جلسه در هفته قالب آماده دارد."))
        if program_type == WorkoutProgram.ProgramType.PUSH_PULL_LEGS and sessions not in {3, 6}:
            self.add_error("sessions_per_week", _("Push/Pull/Leg برای ۳ یا ۶ جلسه در هفته قابل استفاده است."))
        if cleaned.get("requires_payment") and not cleaned.get("price"):
            self.add_error("price", _("برای برنامه پولی مبلغ را وارد کنید."))
        if not cleaned.get("requires_payment"):
            cleaned["price"] = 0
        return cleaned

    def clean_session_movement_targets_json(self):
        value = self.cleaned_data.get("session_movement_targets_json") or []
        if not isinstance(value, list):
            raise forms.ValidationError(_("تنظیمات عضلات هر جلسه معتبر نیست."))
        sessions = self.cleaned_data.get("sessions_per_week") or 0
        if len(value) > sessions:
            raise forms.ValidationError(_("تعداد تنظیمات جلسه‌ها بیشتر از تعداد جلسه‌های هفته است."))
        selected_ids = {
            str(item.pk)
            for item in self.cleaned_data.get("target_secondary_movement_types", [])
        }
        pattern_ids = {
            str(value)
            for value in ExerciseMovementType.objects.values_list("pk", flat=True)
        }
        cleaned = []
        for session in value:
            if not isinstance(session, dict):
                raise forms.ValidationError(_("ساختار تنظیمات عضلات هر جلسه معتبر نیست."))
            normalized_types = []
            normalized_counts = {}
            if "targets" in session:
                raw_targets = session.get("targets")
                if not isinstance(raw_targets, list):
                    raise forms.ValidationError(_("ساختار هدف‌های جلسه معتبر نیست."))
                for entry in raw_targets:
                    if not isinstance(entry, dict):
                        continue
                    raw_id = entry.get(
                        "movement_type",
                        entry.get("movement_type_id", entry.get("target_section")),
                    )
                    try:
                        movement_type_id = str(int(raw_id))
                        count = int(entry.get("count", entry.get("movement_count", 0)))
                    except (TypeError, ValueError):
                        raise forms.ValidationError(_("نوع یا تعداد حرکت جلسه معتبر نیست."))
                    if movement_type_id not in selected_ids:
                        raise forms.ValidationError(_("یکی از بخش‌های جلسه در فهرست هدف‌ها نیست."))
                    if count < 0 or count > 20:
                        raise forms.ValidationError(_("تعداد حرکت جلسه باید بین صفر و بیست باشد."))
                    if movement_type_id not in normalized_types:
                        normalized_types.append(movement_type_id)
                    normalized_counts[movement_type_id] = count
            else:
                movement_types = session.get("movement_types", [])
                counts = session.get("counts", {}) or {}
                if not isinstance(movement_types, list) or not isinstance(counts, dict):
                    raise forms.ValidationError(_("ساختار هدف‌های هر جلسه معتبر نیست."))
                for movement_type_id in movement_types:
                    try:
                        movement_type_id = str(int(movement_type_id))
                    except (TypeError, ValueError):
                        raise forms.ValidationError(_("نوع حرکت دوم انتخاب‌شده معتبر نیست."))
                    if movement_type_id not in selected_ids:
                        raise forms.ValidationError(_("یکی از انواع حرکت دوم جلسه در فهرست هدف‌ها نیست."))
                    if movement_type_id not in normalized_types:
                        normalized_types.append(movement_type_id)
                    raw_count = counts.get(movement_type_id, counts.get(int(movement_type_id), 0))
                    try:
                        count = int(raw_count)
                    except (TypeError, ValueError):
                        raise forms.ValidationError(_("تعداد حرکت جلسه معتبر نیست."))
                    if count < 0 or count > 20:
                        raise forms.ValidationError(_("تعداد حرکت جلسه باید بین صفر و بیست باشد."))
                    normalized_counts[movement_type_id] = count

            raw_patterns = session.get("patterns", [])
            if not isinstance(raw_patterns, list):
                raise forms.ValidationError(_("ساختار الگوی حرکت جلسه معتبر نیست."))
            normalized_patterns = []
            for entry in raw_patterns:
                if not isinstance(entry, dict):
                    continue
                try:
                    pattern_id = str(int(entry.get("movement_pattern", entry.get("id", 0))))
                    count = int(entry.get("count", 0))
                except (TypeError, ValueError):
                    raise forms.ValidationError(_("الگو یا تعداد حرکت معتبر نیست."))
                if pattern_id not in pattern_ids or count < 0 or count > 20:
                    raise forms.ValidationError(_("الگو یا تعداد حرکت معتبر نیست."))
                if count:
                    normalized_patterns.append(
                        {
                            "movement_pattern": pattern_id,
                            "count": count,
                            "label": str(entry.get("label") or "")[:80],
                        }
                    )
            cleaned.append(
                {
                    "name": str(session.get("name") or "")[:80],
                    "patterns": normalized_patterns,
                    "targets": [
                        {
                            "movement_type": movement_type_id,
                            "count": normalized_counts[movement_type_id],
                        }
                        for movement_type_id in normalized_types
                    ],
                    "movement_types": normalized_types,
                    "counts": normalized_counts,
                }
            )
        return cleaned

    def __init__(self, *args, **kwargs):
        actor = kwargs.pop("actor", None)
        super().__init__(*args, **kwargs)
        self.fields["user"].label_from_instance = self._user_label
        if actor is not None:
            from account.services.program_template_service import ProgramTemplateService

            self.fields["template"].queryset = ProgramTemplateService.accessible_to(actor)
        choices = self._database_goal_choices()
        self.fields["athlete_goal"].choices = (
            (("", _("استفاده از هدف پروفایل یا هدف برنامه")),)
            + choices
        )
        self.fields["goal"].choices = choices
        if self.fields["goal"].initial not in {value for value, _ in choices}:
            self.fields["goal"].initial = choices[0][0] if choices else None

    @classmethod
    def _database_goal_choices(cls):
        """Build the automatic-program goals from the managed goal catalog."""
        return ExerciseGoalService.choices()

    @staticmethod
    def _user_label(user):
        name_parts = " ".join(
            value.strip()
            for value in (user.first_name or "", user.last_name or "")
            if value and value.strip()
        )
        name = name_parts or user.fullname or _("بدون نام")
        if name_parts and user.fullname and user.fullname != name_parts:
            name = f"{name} ({user.fullname})"
        role_label = f" · {_('مربی')}" if user.is_coach else ""
        return f"{name} — {user.phone}{role_label}"

    def clean_correctives_json(self):
        value = self.cleaned_data.get("correctives_json") or []
        if not isinstance(value, list):
            raise forms.ValidationError(_("ساختار حرکت‌های اصلاحی معتبر نیست."))
        return value


class ProgramTemplateForm(forms.ModelForm):
    class Meta:
        model = ProgramTemplate
        fields = (
            "title", "training_goal", "difficulty", "days_per_week", "weeks",
            "payload", "is_shared_with_enabled_coaches", "shared_with",
        )
        widgets = {
            "title": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "training_goal": forms.Select(attrs={"class": INPUT_CLASS}),
            "difficulty": forms.Select(attrs={"class": INPUT_CLASS}),
            "days_per_week": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 1, "max": 7, "dir": "ltr"}),
            "weeks": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 1, "max": 18, "dir": "ltr"}),
            "payload": forms.HiddenInput(),
            "is_shared_with_enabled_coaches": forms.CheckboxInput(attrs={"class": "admin-checkbox"}),
            "shared_with": forms.SelectMultiple(attrs={"class": INPUT_CLASS, "size": 6}),
        }

    def __init__(self, *args, **kwargs):
        actor = kwargs.pop("actor", None)
        super().__init__(*args, **kwargs)
        self.fields["shared_with"].queryset = User.objects.filter(
            is_coach=True,
            is_admin=False,
            can_use_automatic_programming=True,
            is_active=True,
        ).order_by("fullname")
        self.fields["days_per_week"].initial = self.fields["days_per_week"].initial or 3
        self.fields["payload"].required = False
        self.fields["payload"].help_text = _(
            "برای هر جلسه بخش‌های هدف و تعداد حرکت را انتخاب کنید؛ قالب فقط ساختار جلسه‌ها را مشخص می‌کند و حرکت نهایی با توجه به تجهیزات و محدودیت ورزشکار انتخاب می‌شود."
        )
        if actor is not None and not getattr(actor, "is_admin", False):
            self.fields["shared_with"].disabled = True
            self.fields["is_shared_with_enabled_coaches"].disabled = True

    def clean_payload(self):
        payload = self.cleaned_data.get("payload") or {}
        if not isinstance(payload, dict):
            raise forms.ValidationError(_("محتوای قالب باید یک شیء JSON باشد."))
        targets = payload.get("session_movement_targets")
        if targets is not None and (not isinstance(targets, list) or len(targets) > 7):
            raise forms.ValidationError(_("تقسیم جلسه‌های قالب باید یک فهرست حداکثر هفت‌تایی باشد."))
        return payload
