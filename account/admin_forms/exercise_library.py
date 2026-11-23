"""Catalog forms for exercises, muscles, and related lookup values."""

import json
import re

from django import forms
from django.core.files.uploadedfile import UploadedFile
from django.utils.translation import gettext_lazy as _

from account.domain.workout_prescriptions import (
    is_valid_repetition_prescription,
    parse_set_prescription,
)
from account.domain.exercise_goals import canonical_goal_key
from account.froms import INPUT_CLASS
from account.models import (
    CardioExercise,
    CorrectiveExercise,
    Exercise,
    Muscle,
    ExerciseAbnormalityType,
    ExerciseEquipmentType,
    ExerciseRepetitionType,
    ExerciseRestType,
    ExerciseSetType,
    ExerciseTrainingMethod,
    MuscleVolumeLandmark,
)
from account.validators import validate_image_upload, validate_media_upload, validate_video_upload
from account.services.exercise_goal_service import ExerciseGoalService


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
