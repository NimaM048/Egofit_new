"""Shared form fields and widgets for Jalali calendar dates."""

import jdatetime
from django import forms
from django.utils.translation import gettext_lazy as _

from account.froms import INPUT_CLASS
from account.utils import normalize_digits


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
