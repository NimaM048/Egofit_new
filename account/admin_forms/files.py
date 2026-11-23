"""Upload forms for athlete documents and media."""

from django import forms
from django.utils.translation import gettext_lazy as _

from account.froms import INPUT_CLASS
from account.models import ClientDocument, ClientMedia
from account.validators import validate_image_upload, validate_pdf_upload, validate_video_upload


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
