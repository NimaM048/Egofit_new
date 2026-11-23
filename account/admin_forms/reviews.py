"""Moderation and review forms for staff workflows."""

from django import forms
from django.utils.translation import gettext_lazy as _

from account.froms import INPUT_CLASS


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
