"""Staff forms for account details, ownership, and role changes."""

from django import forms
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from account.froms import INPUT_CLASS
from account.models import User
from account.utils import calculate_age_from_jalali, combine_fullname, normalize_phone_number


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
