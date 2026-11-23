"""Forms for body measurements and health check-ins."""

import jdatetime
from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from account.froms import INPUT_CLASS
from account.models import (
    BodyCircumferenceMeasurement,
    CaliperMeasurement,
    MeasurementMethod,
    UserHealthRecord,
    ExerciseAbnormalityType,
)
from account.utils import normalize_digits

from .fields import JalaliDateField, _jalali_date_widget


class AdminHealthRecordForm(forms.ModelForm):
    abnormality_type = forms.ModelChoiceField(
        queryset=ExerciseAbnormalityType.objects.order_by("name"),
        required=False,
        label=_("ناهنجاری"),
        empty_label=_("انتخاب ناهنجاری"),
        widget=forms.Select(attrs={"class": INPUT_CLASS, "data-health-record-abnormality": "true"}),
    )
    onset_date = JalaliDateField(
        required=False,
        label=_("تاریخ شروع"),
        help_text=_("تاریخ شمسی را با قالب سال/ماه/روز وارد کنید."),
        widget=_jalali_date_widget(),
    )
    resolved_date = JalaliDateField(
        required=False,
        label=_("تاریخ برطرف‌شدن"),
        help_text=_("تاریخ شمسی را با قالب سال/ماه/روز وارد کنید."),
        widget=_jalali_date_widget(),
    )
    recorded_at = JalaliDateField(
        required=True,
        initial=timezone.localdate,
        label=_("تاریخ ثبت"),
        help_text=_("تاریخ شمسی را با قالب سال/ماه/روز وارد کنید."),
        widget=_jalali_date_widget(),
    )

    class Meta:
        model = UserHealthRecord
        fields = (
            "category", "title", "abnormality_type", "details", "body_region", "affected_side", "severity",
            "onset_date", "status", "resolved_date", "training_restriction",
            "training_restriction_notes", "recorded_at",
        )
        widgets = {
            "category": forms.Select(attrs={"class": INPUT_CLASS, "data-health-record-category": "true"}),
            "title": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": _("مثلاً زانو درد یا داروی مصرفی")}),
            "details": forms.Textarea(attrs={"class": INPUT_CLASS, "rows": 4, "placeholder": _("توضیحات تکمیلی را وارد کنید")}),
            "body_region": forms.Select(attrs={"class": INPUT_CLASS}),
            "affected_side": forms.Select(attrs={"class": INPUT_CLASS}),
            "severity": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 0, "max": 10, "step": 1}),
            "status": forms.Select(attrs={"class": INPUT_CLASS}),
            "training_restriction": forms.CheckboxInput(attrs={"class": "coach-request-checkbox"}),
            "training_restriction_notes": forms.Textarea(attrs={"class": INPUT_CLASS, "rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Older admin submissions did not include lifecycle status. Keep the
        # default active state while allowing those records to be edited.
        self.fields["status"].required = False
        self.fields["title"].required = False
        category = self.data.get(self.add_prefix("category")) if self.is_bound else getattr(self.instance, "category", "")
        self.fields["abnormality_type"].required = category == UserHealthRecord.CategoryChoices.ABNORMALITY
        self.fields["title"].required = category != UserHealthRecord.CategoryChoices.ABNORMALITY

    def clean(self):
        cleaned = super().clean()
        category = cleaned.get("category")
        if category == UserHealthRecord.CategoryChoices.ABNORMALITY:
            abnormality = cleaned.get("abnormality_type")
            if abnormality:
                cleaned["title"] = abnormality.name
            elif "abnormality_type" in self.fields:
                self.add_error("abnormality_type", _("یک ناهنجاری از فهرست انتخاب کنید."))
        else:
            cleaned["abnormality_type"] = None
            if "title" in self.fields and not cleaned.get("title"):
                self.add_error("title", _("عنوان را وارد کنید."))
        if not cleaned.get("status"):
            cleaned["status"] = UserHealthRecord.Status.ACTIVE
        onset = cleaned.get("onset_date")
        resolved = cleaned.get("resolved_date")
        if onset and resolved and resolved < onset:
            self.add_error("resolved_date", _("تاریخ برطرف‌شدن نمی‌تواند پیش از تاریخ شروع باشد."))
        if cleaned.get("training_restriction") and not cleaned.get("training_restriction_notes"):
            self.add_error("training_restriction_notes", _("توضیح کوتاه محدودیت تمرینی را وارد کنید."))
        return cleaned


class DailyMoodForm(forms.Form):
    entry_date = JalaliDateField(
        # Dates are shown and accepted in the Jalali calendar on the profile.
        label=_("تاریخ ثبت"),
        help_text=_("تاریخ را با قالب سال/ماه/روز شمسی وارد کنید."),
        widget=forms.TextInput(
            attrs={
                "class": INPUT_CLASS,
                "dir": "ltr",
                "inputmode": "numeric",
                "placeholder": "۱۴۰۵/۰۸/۲۰",
                "autocomplete": "off",
            }
        ),
    )

    anxiety_score = forms.IntegerField(label=_("میزان اضطراب"), min_value=0, max_value=10, widget=forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 0, "max": 10, "inputmode": "numeric"}))
    motivation_score = forms.IntegerField(label=_("میزان انگیزه"), min_value=0, max_value=10, widget=forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 0, "max": 10, "inputmode": "numeric"}))
    sleep_quality_score = forms.IntegerField(label=_("کیفیت خواب"), min_value=0, max_value=10, widget=forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 0, "max": 10, "inputmode": "numeric"}))
    training_satisfaction_score = forms.IntegerField(label=_("رضایت از کیفیت تمرین"), min_value=0, max_value=10, widget=forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 0, "max": 10, "inputmode": "numeric"}))
    training_readiness_score = forms.IntegerField(
        label=_("آمادگی جسمی برای تمرین"),
        min_value=0,
        max_value=10,
        required=False,
        help_text=_("اختیاری؛ خستگی و کوفتگی امروز را در امتیاز آمادگی لحاظ کنید."),
        widget=forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 0, "max": 10, "inputmode": "numeric"}),
    )
    fatigue_score = forms.IntegerField(
        label=_("خستگی"), min_value=0, max_value=10, required=False,
        help_text=_("۰ یعنی بدون خستگی و ۱۰ یعنی خستگی بسیار زیاد."),
        widget=forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 0, "max": 10, "inputmode": "numeric"}),
    )
    muscle_soreness_score = forms.IntegerField(
        label=_("کوفتگی عضلانی"), min_value=0, max_value=10, required=False,
        help_text=_("۰ یعنی بدون کوفتگی و ۱۰ یعنی کوفتگی بسیار زیاد."),
        widget=forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 0, "max": 10, "inputmode": "numeric"}),
    )
    resting_heart_rate_bpm = forms.IntegerField(
        label=_("ضربان قلب استراحت صبحگاهی"),
        min_value=30,
        max_value=220,
        widget=forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 30, "max": 220, "inputmode": "numeric"}),
    )
    heart_rate_measured_at = forms.DateTimeField(
        required=False,
        label=_("زمان واقعی سنجش ضربان (اختیاری)"),
        help_text=_("اگر زمان دقیق را نمی‌دانید خالی بگذارید؛ زمان ثبت با زمان سنجش یکی فرض نمی‌شود."),
        widget=forms.DateTimeInput(attrs={"class": INPUT_CLASS, "type": "datetime-local", "dir": "ltr"}),
    )

    def clean_entry_date(self):
        value = self.cleaned_data["entry_date"]
        if value > timezone.localdate():
            raise forms.ValidationError(_("تاریخ ثبت نمی‌تواند در آینده باشد."))
        return value

    def clean_heart_rate_measured_at(self):
        value = self.cleaned_data.get("heart_rate_measured_at")
        if value and timezone.is_naive(value):
            value = timezone.make_aware(value, timezone.get_current_timezone())
        if value and value > timezone.now():
            raise forms.ValidationError(_("زمان سنجش نمی‌تواند در آینده باشد."))
        return value


def _clean_measurement_date(raw):
    raw = normalize_digits(str(raw or "").strip())
    if not raw:
        return raw
    parts = raw.replace("-", "/").split("/")
    try:
        if len(parts) == 3 and parts[0].isdigit() and 1300 <= int(parts[0]) <= 1600:
            date_value = jdatetime.date(*(int(part) for part in parts)).togregorian()
        else:
            from django.utils.dateparse import parse_date

            date_value = parse_date(raw)
    except (TypeError, ValueError):
        date_value = None
    if date_value is None:
        raise forms.ValidationError(_("تاریخ را به شکل YYYY/MM/DD شمسی یا YYYY-MM-DD میلادی وارد کنید."))
    if date_value > timezone.localdate():
        raise forms.ValidationError(_("تاریخ اندازه‌گیری نمی‌تواند در آینده باشد."))
    return raw


class AdminCircumferenceForm(forms.ModelForm):
    class Meta:
        model = BodyCircumferenceMeasurement
        exclude = ("user", "recorded_at", "measured_at", "measurement_source", "measured_by")
        widgets = {
            "measured_at_jalali": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": _("تاریخ ثبت (فرمت: سال/ماه/روز)")}),
            "measurement_method": forms.Select(attrs={"class": INPUT_CLASS}),
            "protocol_version": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": _("برای نمونه: tape-v1")}),
            "trial_number": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 1, "step": 1, "dir": "ltr"}),
            **{field: forms.NumberInput(attrs={"class": INPUT_CLASS, "step": "0.1", "dir": "ltr", "data-select-all-on-focus": "true"}) for field in (
                "height_cm",
                "weight_kg",
                "wrist_cm",
                "forearm_cm",
                "arm_rest_cm",
                "arm_flexed_cm",
                "wrist_left_cm",
                "forearm_left_cm",
                "arm_rest_left_cm",
                "arm_flexed_left_cm",
                "head_cm",
                "neck_cm",
                "chest_cm",
                "shoulders_cm",
                "waist_cm",
                "abdomen_cm",
                "hips_cm",
                "thigh_cm",
                "calf_cm",
                "thigh_left_cm",
                "calf_left_cm",
                "sit_height_cm",
                "leg_length_cm",
                "shoulder_width_cm",
                "hip_width_cm",
                "elbow_width_cm",
                "knee_width_cm",
                "arm_length_cm",
            )},
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["measurement_method"].required = False
        self.fields["trial_number"].required = False

    def clean(self):
        cleaned = super().clean()
        cleaned.setdefault("measurement_method", MeasurementMethod.TAPE)
        if not cleaned.get("measurement_method"):
            cleaned["measurement_method"] = MeasurementMethod.TAPE
        if not cleaned.get("trial_number"):
            cleaned["trial_number"] = 1
        for name, field in self.fields.items():
            if not isinstance(field, forms.DecimalField):
                continue
            value = cleaned.get(name)
            if value is not None and value <= 0:
                self.add_error(name, _("مقدار باید بزرگ‌تر از صفر باشد."))
        weight = cleaned.get("weight_kg")
        height = cleaned.get("height_cm")
        if weight is not None and not 20 <= weight <= 400:
            self.add_error("weight_kg", _("وزن باید بین ۲۰ و ۴۰۰ کیلوگرم باشد."))
        if height is not None and not 50 <= height <= 260:
            self.add_error("height_cm", _("قد باید بین ۵۰ و ۲۶۰ سانتی‌متر باشد."))
        return cleaned

    def clean_measured_at_jalali(self):
        return _clean_measurement_date(self.cleaned_data.get("measured_at_jalali"))


class AdminCaliperForm(forms.ModelForm):
    class Meta:
        model = CaliperMeasurement
        exclude = ("user", "recorded_at", "measured_at", "measurement_source", "measured_by")
        widgets = {
            "measured_at_jalali": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": _("تاریخ ثبت (فرمت: سال/ماه/روز)")}),
            "measurement_method": forms.Select(attrs={"class": INPUT_CLASS}),
            "protocol_version": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": _("برای نمونه: skinfold-v1")}),
            "trial_number": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 1, "step": 1, "dir": "ltr"}),
            **{field: forms.NumberInput(attrs={"class": INPUT_CLASS, "step": "0.1", "dir": "ltr", "data-select-all-on-focus": "true"}) for field in (
                "chest_armpit_men_mm",
                "axilla_mm",
                "subscapular_mm",
                "abdominal_mm",
                "suprailiac_mm",
                "chest_mm",
                "biceps_mm",
                "triceps_mm",
                "thigh_mm",
                "calf_mm",
            )},
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["measurement_method"].required = False
        self.fields["trial_number"].required = False

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("measurement_method"):
            cleaned["measurement_method"] = MeasurementMethod.SKINFOLD_CALIPER
        if not cleaned.get("trial_number"):
            cleaned["trial_number"] = 1
        for name, field in self.fields.items():
            if not isinstance(field, forms.DecimalField):
                continue
            value = cleaned.get(name)
            if value is not None and value <= 0:
                self.add_error(name, _("مقدار باید بزرگ‌تر از صفر باشد."))
        return cleaned

    def clean_measured_at_jalali(self):
        return _clean_measurement_date(self.cleaned_data.get("measured_at_jalali"))
