import json
import re

import jdatetime
from django import forms
from django.core.files.uploadedfile import UploadedFile
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from account.domain.workout_prescriptions import (
    is_valid_repetition_prescription,
    parse_set_prescription,
)
from account.domain.exercise_goals import canonical_goal_key
from account.froms import INPUT_CLASS
from account.models import (
    BodyCircumferenceMeasurement,
    CaliperMeasurement,
    ClientDocument,
    ClientMedia,
    CardioExercise,
    CorrectiveExercise,
    Exercise,
    ExerciseMovementType,
    ExerciseSecondaryMovementType,
    Muscle,
    MeasurementMethod,
    User,
    UserHealthRecord,
    WorkoutProgram,
    ExerciseAbnormalityType,
    ExerciseDifficultyLevel,
    ExerciseEquipmentType,
    ExerciseRepetitionType,
    ExerciseRestType,
    ExerciseSetType,
    ExerciseTrainingMethod,
    TrainingGoal,
    ProgramTemplate,
    MuscleVolumeLandmark,
)
from account.utils import calculate_age_from_jalali, combine_fullname, normalize_digits, normalize_phone_number
from account.validators import validate_image_upload, validate_media_upload, validate_pdf_upload, validate_video_upload
from account.services.exercise_goal_service import ExerciseGoalService


class AdminUserSearchForm(forms.Form):
    query = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": INPUT_CLASS,
                "placeholder": _("نام یا شماره همراه کاربر را وارد کنید"),
            }
        ),
    )


class AdminCommentModerationForm(forms.Form):
    action = forms.ChoiceField(
        choices=(
            ("approve", _("تایید و انتشار")),
            ("reject", _("رد و عدم انتشار")),
            ("respond", _("ثبت پاسخ")),
        ),
        widget=forms.HiddenInput,
    )
    response = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": INPUT_CLASS,
                "rows": 3,
                "placeholder": _("پاسخ مدیر (اختیاری)") ,
            }
        ),
    )

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("action") == "respond" and not (cleaned_data.get("response") or "").strip():
            self.add_error("response", _("برای ثبت پاسخ، متن پاسخ را وارد کنید."))
        return cleaned_data


class AdminPersonalInfoForm(forms.ModelForm):
    class Meta:
        model = User
        fields = (
            "first_name",
            "last_name",
            "display_name",
            "phone",
            "blood_group",
            "gender",
            "birth_date_jalali",
            "activity_level",
            "coach",
        )
        widgets = {
            "first_name": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": _("نام")}),
            "last_name": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": _("نام خانوادگی")}),
            "display_name": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": _("نام کاربری")}),
            "phone": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": _("شماره تماس"), "dir": "ltr"}),
            "blood_group": forms.Select(attrs={"class": INPUT_CLASS}),
            "gender": forms.Select(attrs={"class": INPUT_CLASS}),
            "birth_date_jalali": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": _("تاریخ تولد (فرمت: سال/ماه/روز)")}),
            "activity_level": forms.Select(attrs={"class": INPUT_CLASS}),
            "coach": forms.Select(attrs={"class": INPUT_CLASS}),
        }
        labels = {
            "display_name": _("نام کاربری"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "coach" in self.fields:
            self.fields["coach"].queryset = User.objects.filter(Q(is_admin=True) | Q(is_coach=True), is_active=True)
            self.fields["coach"].required = False
            self.fields["coach"].empty_label = _("بدون مربی")

    def clean(self):
        cleaned_data = super().clean()
        first_name = cleaned_data.get("first_name", "")
        last_name = cleaned_data.get("last_name", "")
        combined = combine_fullname(first_name, last_name)
        if combined and User.objects.filter(fullname=combined).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError(_("این نام و نام خانوادگی قبلاً ثبت شده است."))
        cleaned_data["fullname"] = combined
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.cleaned_data.get("fullname"):
            instance.fullname = self.cleaned_data["fullname"]
        instance.age = calculate_age_from_jalali(self.cleaned_data.get("birth_date_jalali"))
        if commit:
            instance.save()
        return instance


class JalaliDateField(forms.DateField):
    """Accept Jalali text, return Gregorian ``date`` values for model fields."""

    def to_python(self, value):
        if value in self.empty_values:
            return None
        if hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day"):
            return value

        raw = normalize_digits(str(value).strip())
        parts = raw.replace("-", "/").split("/")
        if len(parts) == 3 and parts[0].isdigit() and 1300 <= int(parts[0]) <= 1600:
            try:
                return jdatetime.date(int(parts[0]), int(parts[1]), int(parts[2])).togregorian()
            except ValueError as exc:
                raise forms.ValidationError(_("تاریخ شمسی معتبر نیست.")) from exc
        return super().to_python(raw)

    def prepare_value(self, value):
        if hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day"):
            try:
                return jdatetime.date.fromgregorian(date=value).strftime("%Y/%m/%d")
            except (TypeError, ValueError):
                return value
        return value


def _jalali_date_widget():
    return forms.TextInput(
        attrs={
            "class": INPUT_CLASS,
            "dir": "ltr",
            "inputmode": "numeric",
            "placeholder": "۱۴۰۵/۰۱/۰۱",
            "autocomplete": "off",
        }
    )


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


class AdminUserCreateForm(forms.ModelForm):
    password = forms.CharField(
        label=_("رمز عبور (برای ورود به اپ)"),
        widget=forms.TextInput(attrs={"class": INPUT_CLASS, "dir": "ltr"}),
        help_text=_("برای ورود کاربر به اپ استفاده می‌شود"),
    )

    class Meta:
        model = User
        fields = ("first_name", "last_name", "phone", "blood_group", "gender", "birth_date_jalali", "activity_level", "coach")
        widgets = {
            "first_name": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": _("نام")}),
            "last_name": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": _("نام خانوادگی")}),
            "phone": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": _("شماره تماس"), "dir": "ltr"}),
            "blood_group": forms.Select(attrs={"class": INPUT_CLASS}),
            "gender": forms.Select(attrs={"class": INPUT_CLASS}),
            "birth_date_jalali": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": _("تاریخ تولد (فرمت: سال/ماه/روز)")}),
            "activity_level": forms.Select(attrs={"class": INPUT_CLASS}),
            "coach": forms.Select(attrs={"class": INPUT_CLASS}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "coach" in self.fields:
            self.fields["coach"].queryset = User.objects.filter(Q(is_admin=True) | Q(is_coach=True), is_active=True)
            self.fields["coach"].required = False
            self.fields["coach"].empty_label = _("بدون مربی")

    def clean_phone(self):
        normalized = normalize_phone_number(self.cleaned_data["phone"])
        if len(normalized) != 11 or not normalized.isdigit():
            raise forms.ValidationError(_("شماره تماس معتبر نیست."))
        return normalized

    def clean(self):
        cleaned_data = super().clean()
        combined = combine_fullname(cleaned_data.get("first_name", ""), cleaned_data.get("last_name", ""))
        if combined and User.objects.filter(fullname=combined).exists():
            raise forms.ValidationError(_("این نام و نام خانوادگی قبلاً ثبت شده است."))
        cleaned_data["fullname"] = combined
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.fullname = self.cleaned_data["fullname"]
        instance.age = calculate_age_from_jalali(self.cleaned_data.get("birth_date_jalali"))
        if commit:
            instance.save()
        return instance


class CoachAthleteCreateForm(AdminUserCreateForm):
    class Meta(AdminUserCreateForm.Meta):
        fields = (
            "first_name",
            "last_name",
            "phone",
            "blood_group",
            "gender",
            "birth_date_jalali",
            "activity_level",
        )


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


class AdminClientMediaForm(forms.ModelForm):
    class Meta:
        model = ClientMedia
        fields = ("image", "video")
        widgets = {
            "image": forms.ClearableFileInput(attrs={"class": INPUT_CLASS}),
            "video": forms.ClearableFileInput(attrs={"class": INPUT_CLASS}),
        }

    def clean_image(self):
        return validate_image_upload(self.cleaned_data.get("image"))

    def clean_video(self):
        return validate_video_upload(self.cleaned_data.get("video"))


class AdminClientDocumentForm(forms.ModelForm):
    access_type = forms.MultipleChoiceField(
        choices=(("paid", _("پرداخت شده — دانلود بدون هزینه")), ("unpaid", _("پرداخت نشده — نیازمند پرداخت"))),
        required=False,
        label=_("نوع دسترسی"),
        widget=forms.CheckboxSelectMultiple(attrs={"class": "admin-access-checkboxes"}),
    )
    class Meta:
        model = ClientDocument
        fields = ("title", "file", "price")
        widgets = {
            "title": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": _("مثلاً: برنامه تمرینی هفته ۱")}),
            "file": forms.ClearableFileInput(attrs={"class": INPUT_CLASS}),
            "price": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 0, "step": 1, "inputmode": "numeric", "dir": "ltr"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["price"].required = False
        if self.instance and self.instance.pk:
            self.fields["access_type"].initial = ["paid" if self.instance.requires_payment else "unpaid"]
        else:
            self.fields["access_type"].initial = ["paid"]

    def clean_file(self):
        return validate_pdf_upload(self.cleaned_data.get("file"))

    def clean(self):
        cleaned = super().clean()
        # Preserve the legacy upload behavior when older clients submit without
        # the new access selector: those files were free to download.
        access_types = cleaned.get("access_type") or ["paid"]
        if len(access_types) != 1:
            self.add_error("access_type", _("فقط یکی از گزینه‌های پرداختی یا رایگان را انتخاب کنید."))
        cleaned["access_type"] = access_types
        if access_types == ["unpaid"] and not cleaned.get("price"):
            self.add_error("price", _("برای فایل پولی مبلغ را وارد کنید."))
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.requires_payment = "unpaid" in self.cleaned_data.get("access_type", [])
        if not instance.requires_payment:
            instance.price = 0
        if commit:
            instance.save()
        return instance


class AdminRoleToggleForm(forms.Form):
    is_admin = forms.BooleanField(required=False, label=_("نقش ادمین"))


class AdminCoachRoleToggleForm(forms.Form):
    is_coach = forms.BooleanField(required=False, label=_("نقش مربی"))


class AdminBulkCoachAssignForm(forms.Form):
    coach = forms.ModelChoiceField(
        queryset=User.objects.filter(Q(is_admin=True) | Q(is_coach=True), is_active=True).order_by("fullname"),
        required=True,
        label=_("مربی"),
        widget=forms.Select(attrs={"class": INPUT_CLASS}),
    )


class AdminUserCoachForm(forms.Form):
    coach = forms.ModelChoiceField(
        queryset=User.objects.filter(Q(is_admin=True) | Q(is_coach=True), is_active=True).order_by("fullname"),
        required=False,
        label=_("مربی"),
        empty_label=_("بدون مربی"),
        widget=forms.Select(attrs={"class": INPUT_CLASS}),
    )


class AdminRoleSetForm(forms.Form):
    """Assign one of the three portal roles in a single action."""

    role = forms.ChoiceField(
        choices=User.Role.choices,
        label=_("نقش کاربر"),
        widget=forms.Select(attrs={"class": INPUT_CLASS}),
    )


class AdminReviewForm(forms.Form):
    """Shared approve/reject payload for coach applications and exercise suggestions."""

    action = forms.ChoiceField(
        choices=(("approve", _("تایید")), ("reject", _("رد"))),
        label=_("عملیات"),
        widget=forms.Select(attrs={"class": INPUT_CLASS}),
    )
    reason = forms.CharField(
        required=False,
        label=_("دلیل رد"),
        widget=forms.Textarea(
            attrs={"class": INPUT_CLASS + " h-auto min-h-[110px] py-4", "rows": 3}
        ),
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("action") == "reject" and not (cleaned.get("reason") or "").strip():
            self.add_error("reason", _("برای رد کردن، وارد کردن دلیل الزامی است."))
        return cleaned


class AdminCoachApplicationReviewForm(AdminReviewForm):
    pass


class AdminExerciseSuggestionReviewForm(AdminReviewForm):
    pass


class LibrarySearchForm(forms.Form):
    query = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": INPUT_CLASS,
                "placeholder": _("نام را وارد کنید"),
            }
        ),
    )


class MuscleForm(forms.ModelForm):
    class Meta:
        model = Muscle
        fields = ("name", "name_en", "origin", "insertion", "nerve", "function_note", "image")
        widgets = {
            "name": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": _("نام عضله")}),
            "name_en": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": _("نام انگلیسی عضله"), "dir": "ltr"}),
            "origin": forms.Textarea(attrs={"class": INPUT_CLASS, "rows": 2}),
            "insertion": forms.Textarea(attrs={"class": INPUT_CLASS, "rows": 2}),
            "nerve": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "function_note": forms.Textarea(attrs={"class": INPUT_CLASS, "rows": 2}),
            "image": forms.ClearableFileInput(attrs={"class": INPUT_CLASS}),
        }

    def clean_image(self):
        uploaded_file = self.cleaned_data.get("image")
        return validate_image_upload(uploaded_file) if isinstance(uploaded_file, UploadedFile) else uploaded_file


class LookupCatalogModelForm(forms.ModelForm):
    class Meta:
        pass

    def clean_method(self):
        return self.cleaned_data.get("method") or ExerciseTrainingMethod.NORMAL

    reps = forms.CharField(
        max_length=100,
        label=_("تعداد تکرار"),
        widget=forms.TextInput(attrs={"class": INPUT_CLASS, "dir": "ltr"}),
    )

    def clean_reps(self):
        value = self.cleaned_data["reps"]
        return re.sub(r"\s*([.+<>])\s*", r"\1", value)

    def clean(self):
        cleaned = super().clean()
        model = self._meta.model
        if model is ExerciseSetType and "set_count" in cleaned:
            if parse_set_prescription(cleaned["set_count"]) is None:
                self.add_error(
                    "set_count",
                    _("تعداد ست را به‌صورت عدد مثبت یا بازهٔ صعودی مانند ۲ یا ۲-۳ وارد کنید."),
                )
        elif model is ExerciseRepetitionType and "reps" in cleaned:
            method = cleaned.get("method") or ExerciseTrainingMethod.NORMAL
            if not is_valid_repetition_prescription(cleaned["reps"], method, cleaned.get("goal", "")):
                self.add_error(
                    "reps",
                    _(
                        "الگوی تکرار با روش انتخاب‌شده سازگار نیست: Pyramid و Reverse Pyramid به ۴ تا ۱۰ عدد با . یا +، "
                        "Low To High به ۳ تا ۱۰ عدد نزولی و High To Low به ۳ تا ۱۰ عدد صعودی با +، "
                        "Drop Set به ۲ یا ۳ عدد با < یا >، "
                        "و 21 Reps دقیقاً به ۷+۷+۷ نیاز دارد. برای روش معمولی بازه‌ای مانند ۶-۱۲ وارد کنید؛ "
                        "برای هدف هوازی مدت را مانند ۱۵ دقیقه یا ۴۵ ثانیه بنویسید."
                    ),
                )
        return cleaned


def build_lookup_form(model, *, fields=("name", "name_en"), data=None, instance=None):
    widgets = {
        field: forms.TextInput(
            attrs={
                "class": INPUT_CLASS,
                **(
                    {"dir": "ltr"}
                    if field.endswith("_en") or (model is ExerciseRepetitionType and field == "reps")
                    else {}
                ),
            }
        )
        for field in fields
    }
    if model is ExerciseEquipmentType and "load_increment_kg" in fields:
        widgets["load_increment_kg"] = forms.NumberInput(
            attrs={"class": INPUT_CLASS, "min": "0.01", "step": "0.25", "dir": "ltr"}
        )
    if model in {ExerciseSetType, ExerciseRepetitionType, ExerciseRestType}:
        widgets["method"] = forms.Select(attrs={"class": INPUT_CLASS})
    base_form = (
        LookupCatalogModelForm
        if model in {ExerciseSetType, ExerciseRepetitionType, ExerciseRestType}
        else forms.ModelForm
    )
    form_class = forms.modelform_factory(model, form=base_form, fields=fields, widgets=widgets)
    form = form_class(data, instance=instance)
    if model in {ExerciseSetType, ExerciseRepetitionType, ExerciseRestType}:
        form.fields["method"].required = False
        goal_choices = [("", _("بدون هدف؛ فقط برای سازگاری با نسخه‌های قدیمی"))]
        goal_choices.extend(ExerciseGoalService.choices())
        existing_goal = canonical_goal_key(getattr(instance, "goal", "") or "")
        if existing_goal and existing_goal not in {value for value, _label in goal_choices}:
            goal_choices.append((existing_goal, existing_goal))
        form.fields["goal"] = forms.ChoiceField(
            choices=goal_choices,
            required=False,
            initial="general",
            label=_("هدف برنامه"),
            widget=forms.Select(attrs={"class": INPUT_CLASS}),
            help_text=_(
                "برای برنامه‌ریزی خودکار، هدف باید با هدف انتخاب‌شده در فرم برنامه‌ریزی یکسان باشد."
            ),
        )
        form.fields["method"].help_text = _(
            "برای هر ترکیب هدف و روش، کتابخانه باید حداقل یک ست، یک تکرار و یک استراحت داشته باشد."
        )
    if model is ExerciseSetType:
        form.fields["set_count"].help_text = _(
            "عدد ست کاری یا بازهٔ صعودی وارد کنید. برای قدرت، ۲ تا ۳ ست در هر حرکت نقطهٔ شروع مناسبی است؛ "
            "حجم عضله را با مجموع ست‌های هفتگی بسنجید، نه فقط یک حرکت."
        )
    if model is ExerciseRepetitionType:
        repetition_help_texts = {
            ExerciseTrainingMethod.NORMAL: _(
                "بازهٔ صعودی مانند ۶-۱۲ وارد کنید؛ برای هدف هوازی مدت را مانند ۱۵ دقیقه یا ۴۵ ثانیه بنویسید."
            ),
            ExerciseTrainingMethod.PYRAMID: _(
                "۴ تا ۱۰ عدد با نقطه یا + وارد کنید؛ نمونه: ۵.۱۰.۱۵.۱۰.۵."
            ),
            ExerciseTrainingMethod.REVERSE_PYRAMID: _(
                "۴ تا ۱۰ عدد با نقطه یا + وارد کنید؛ نمونه: ۱۵.۱۰.۵.۱۰.۱۵."
            ),
            ExerciseTrainingMethod.LOW_TO_HIGH: _(
                "۳ تا ۱۰ عدد نزولی با + وارد کنید؛ نمونه: ۱۵+۱۰+۶+۴."
            ),
            ExerciseTrainingMethod.HIGH_TO_LOW: _(
                "۳ تا ۱۰ عدد صعودی با + وارد کنید؛ نمونه: ۱+۳+۵+۷."
            ),
            ExerciseTrainingMethod.DROP_SET: _(
                "۲ یا ۳ عدد با < یا > جدا کنید؛ نمونه: ۵>۸."
            ),
            ExerciseTrainingMethod.TWENTY_ONE_REPS: _(
                "دقیقاً ۷+۷+۷ وارد کنید."
            ),
        }
        form.fields["reps"].help_text = repetition_help_texts[ExerciseTrainingMethod.NORMAL]
        form.fields["method"].widget.attrs["data-repetition-help"] = json.dumps(
            {method: str(help_text) for method, help_text in repetition_help_texts.items()},
            ensure_ascii=False,
        )
    if model is ExerciseRestType:
        form.fields["rest_time"].help_text = _(
            "زمان را برحسب ثانیه ثبت کنید؛ برای نمونه ۶۰ یا ۶۰-۹۰ ثانیه."
        )
    return form


class ExerciseForm(forms.ModelForm):
    class Meta:
        model = Exercise
        fields = (
            "name",
            "name_en",
            "primary_muscle",
            "secondary_muscle",
            "body_part",
            "movement_type",
            "joint_type",
            "power_type",
            "difficulty_level",
            "equipment_type",
            "required_equipment_types",
            "excluded_abnormalities",
            "execution_equipment_type",
            "secondary_movement_type",
            "pressure_type",
            "description",
            "media",
            "video_1",
            "video_2",
            "video_3",
        )
        widgets = {
            "name": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": _("نام حرکت")}),
            "name_en": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": _("نام انگلیسی حرکت"), "dir": "ltr"}),
            "primary_muscle": forms.Select(attrs={"class": INPUT_CLASS}),
            "secondary_muscle": forms.Select(attrs={"class": INPUT_CLASS}),
            "body_part": forms.Select(attrs={"class": INPUT_CLASS}),
            "movement_type": forms.Select(attrs={"class": INPUT_CLASS}),
            "joint_type": forms.Select(attrs={"class": INPUT_CLASS}),
            "power_type": forms.Select(attrs={"class": INPUT_CLASS}),
            "difficulty_level": forms.Select(attrs={"class": INPUT_CLASS}),
            "equipment_type": forms.Select(attrs={"class": INPUT_CLASS}),
            "required_equipment_types": forms.SelectMultiple(attrs={"class": INPUT_CLASS, "size": 5}),
            "excluded_abnormalities": forms.SelectMultiple(attrs={"class": INPUT_CLASS, "size": 5}),
            "execution_equipment_type": forms.Select(attrs={"class": INPUT_CLASS}),
            "secondary_movement_type": forms.Select(attrs={"class": INPUT_CLASS}),
            "pressure_type": forms.Select(attrs={"class": INPUT_CLASS}),
            "description": forms.Textarea(attrs={"class": INPUT_CLASS, "rows": 3, "placeholder": _("توضیحات حرکت")}),
            "media": forms.ClearableFileInput(
                attrs={"class": INPUT_CLASS, "accept": "image/*,video/*"}
            ),
            "video_1": forms.ClearableFileInput(
                attrs={"class": INPUT_CLASS, "accept": "video/*"}
            ),
            "video_2": forms.ClearableFileInput(
                attrs={"class": INPUT_CLASS, "accept": "video/*"}
            ),
            "video_3": forms.ClearableFileInput(
                attrs={"class": INPUT_CLASS, "accept": "video/*"}
            ),
        }
        labels = {
            "media": _("تصویر یا ویدیوی قدیمی"),
            "video_1": _("ویدیوی ۱"),
            "video_2": _("ویدیوی ۲"),
            "video_3": _("ویدیوی ۳"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["secondary_muscle"].required = False
        self.fields["secondary_muscle"].empty_label = _("بدون عضله فرعی")
        self.fields["pressure_type"].required = False
        self.fields["pressure_type"].empty_label = _("نامشخص")
        self.fields["execution_equipment_type"].required = False
        self.fields["execution_equipment_type"].empty_label = _("نامشخص")
        self.fields["secondary_movement_type"].required = False
        self.fields["secondary_movement_type"].empty_label = _("نامشخص")
        self.fields["required_equipment_types"].queryset = ExerciseEquipmentType.objects.order_by("name")
        self.fields["excluded_abnormalities"].queryset = ExerciseAbnormalityType.objects.order_by("name")

    def clean_media(self):
        uploaded_file = self.cleaned_data.get("media")
        return validate_media_upload(uploaded_file) if isinstance(uploaded_file, UploadedFile) else uploaded_file

    def clean_video_1(self):
        uploaded_file = self.cleaned_data.get("video_1")
        return validate_video_upload(uploaded_file) if isinstance(uploaded_file, UploadedFile) else uploaded_file

    def clean_video_2(self):
        uploaded_file = self.cleaned_data.get("video_2")
        return validate_video_upload(uploaded_file) if isinstance(uploaded_file, UploadedFile) else uploaded_file

    def clean_video_3(self):
        uploaded_file = self.cleaned_data.get("video_3")
        return validate_video_upload(uploaded_file) if isinstance(uploaded_file, UploadedFile) else uploaded_file


class CardioExerciseForm(forms.ModelForm):
    class Meta:
        model = CardioExercise
        fields = ("name", "name_en", "performance_type", "required_equipment_types", "image")
        widgets = {
            "name": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": _("نام حرکت")} ),
            "name_en": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": _("نام انگلیسی حرکت"), "dir": "ltr"}),
            "performance_type": forms.Select(attrs={"class": INPUT_CLASS}),
            "required_equipment_types": forms.SelectMultiple(attrs={"class": INPUT_CLASS, "size": 5}),
            "image": forms.ClearableFileInput(attrs={"class": INPUT_CLASS, "accept": "image/*"}),
        }
        labels = {"image": _("تصویر حرکت")}

    def clean_image(self):
        uploaded_file = self.cleaned_data.get("image")
        return validate_image_upload(uploaded_file) if isinstance(uploaded_file, UploadedFile) else uploaded_file


class CorrectiveExerciseForm(forms.ModelForm):
    class Meta:
        model = CorrectiveExercise
        fields = (
            "name",
            "equipment",
            "abnormality_type",
            "description",
            "media",
            "video_1",
            "video_2",
            "video_3",
        )
        widgets = {
            "name": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": _("نام حرکت")}),
            "equipment": forms.Select(attrs={"class": INPUT_CLASS}),
            "abnormality_type": forms.Select(attrs={"class": INPUT_CLASS}),
            "description": forms.Textarea(attrs={"class": INPUT_CLASS, "rows": 3, "placeholder": _("توضیحات")}),
            "media": forms.ClearableFileInput(
                attrs={"class": INPUT_CLASS, "accept": "image/*,video/*"}
            ),
            "video_1": forms.ClearableFileInput(
                attrs={"class": INPUT_CLASS, "accept": "video/*"}
            ),
            "video_2": forms.ClearableFileInput(
                attrs={"class": INPUT_CLASS, "accept": "video/*"}
            ),
            "video_3": forms.ClearableFileInput(
                attrs={"class": INPUT_CLASS, "accept": "video/*"}
            ),
        }
        labels = {
            "media": _("تصویر یا ویدیوی قدیمی"),
            "video_1": _("ویدیوی ۱"),
            "video_2": _("ویدیوی ۲"),
            "video_3": _("ویدیوی ۳"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["equipment"].required = False
        self.fields["equipment"].empty_label = _("نامشخص")
        self.fields["abnormality_type"].required = False
        self.fields["abnormality_type"].empty_label = _("نامشخص")

    def clean_media(self):
        uploaded_file = self.cleaned_data.get("media")
        return validate_media_upload(uploaded_file) if isinstance(uploaded_file, UploadedFile) else uploaded_file

    def clean_video_1(self):
        uploaded_file = self.cleaned_data.get("video_1")
        return validate_video_upload(uploaded_file) if isinstance(uploaded_file, UploadedFile) else uploaded_file

    def clean_video_2(self):
        uploaded_file = self.cleaned_data.get("video_2")
        return validate_video_upload(uploaded_file) if isinstance(uploaded_file, UploadedFile) else uploaded_file

    def clean_video_3(self):
        uploaded_file = self.cleaned_data.get("video_3")
        return validate_video_upload(uploaded_file) if isinstance(uploaded_file, UploadedFile) else uploaded_file


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
            "requires_payment": forms.CheckboxInput(attrs={"class": "admin-checkbox"}),
            "price": forms.NumberInput(
                attrs={"class": INPUT_CLASS, "min": 0, "step": 1, "inputmode": "numeric", "dir": "ltr"}
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


class MuscleVolumeLandmarkForm(forms.ModelForm):
    class Meta:
        model = MuscleVolumeLandmark
        fields = ("muscle", "training_goal", "experience_band", "mev", "mav", "mrv")
        widgets = {
            "muscle": forms.Select(attrs={"class": INPUT_CLASS}),
            "training_goal": forms.Select(attrs={"class": INPUT_CLASS}),
            "experience_band": forms.Select(attrs={"class": INPUT_CLASS}),
            "mev": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 0, "max": 40, "dir": "ltr"}),
            "mav": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 0, "max": 40, "dir": "ltr"}),
            "mrv": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 0, "max": 40, "dir": "ltr"}),
        }

    def clean(self):
        cleaned = super().clean()
        muscle = cleaned.get("muscle")
        training_goal = cleaned.get("training_goal")
        experience_band = cleaned.get("experience_band")
        if muscle and training_goal and experience_band:
            duplicate = MuscleVolumeLandmark.objects.filter(
                muscle=muscle,
                training_goal=training_goal,
                experience_band=experience_band,
            )
            if self.instance.pk:
                duplicate = duplicate.exclude(pk=self.instance.pk)
            if duplicate.exists():
                raise forms.ValidationError(
                    _("برای همین عضله، هدف و سطح تجربه قبلاً مقدار ثبت شده است؛ مورد موجود را ویرایش کنید.")
                )
        mev, mav, mrv = cleaned.get("mev"), cleaned.get("mav"), cleaned.get("mrv")
        if None not in (mev, mav, mrv) and not mev <= mav <= mrv:
            raise forms.ValidationError(_("مقادیر باید به ترتیب MEV ≤ MAV ≤ MRV باشند."))
        return cleaned


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
