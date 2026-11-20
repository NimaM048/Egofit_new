from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from .models import ArticleBlogModel, Comment, CommentSectionModel, Reply
from .rich_text import sanitize_rich_text
from .validators import (
    validate_comment_name,
    validate_comment_text,
    validate_phone_number,
    validate_reply_text,
    validate_series_comment_text,
)


class CommentForm(forms.ModelForm):
    phone = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-input enterprise-input contact-phone-input',
            'placeholder': 'شماره تماس',
            'inputmode': 'tel',
            'autocomplete': 'tel',
            'dir': 'ltr',
        }),
    )

    class Meta:
        model = Comment
        fields = ['comment', 'name', 'email', 'phone']
        widgets = {
            'comment': forms.Textarea(attrs={
                'class': 'form-textarea enterprise-input enterprise-textarea',
                'placeholder': 'پیام خود را با جزئیات کافی بنویسید',
                'rows': 6,
            }),
            'name': forms.TextInput(attrs={
                'class': 'form-input enterprise-input',
                'placeholder': 'نام و نام خانوادگی',
            }),
            'email': forms.EmailInput(attrs={
                'class': 'form-input enterprise-input',
                'placeholder': 'ایمیل',
            }),
            'phone': forms.TextInput(attrs={
                'class': 'form-input enterprise-input',
                'placeholder': 'شماره تماس',
            }),
        }

    def clean_name(self):
        return validate_comment_name(self.cleaned_data.get('name'))

    def clean_comment(self):
        return validate_comment_text(self.cleaned_data.get('comment'))

    def clean_phone(self):
        return validate_phone_number(self.cleaned_data.get('phone'))


class CommentSectionForm(forms.ModelForm):
    class Meta:
        model = CommentSectionModel
        fields = ('text',)
        widgets = {
            'text': forms.Textarea(attrs={
                'class': 'form-textarea enterprise-input enterprise-textarea',
                'placeholder': 'دیدگاه یا پرسش خود را دقیق و شفاف بنویسید',
                'rows': 5,
            }),
        }
        labels = {'text': ''}

    def clean_text(self):
        return validate_series_comment_text(self.cleaned_data.get('text'))


class ReplyForm(forms.ModelForm):
    class Meta:
        model = Reply
        fields = ('text',)
        widgets = {
            'text': forms.Textarea(attrs={
                'class': 'form-textarea enterprise-input enterprise-textarea',
                'placeholder': 'پاسخ خود را بنویسید...',
                'rows': 3,
            }),
        }
        labels = {'text': ''}

    def clean_text(self):
        return validate_reply_text(self.cleaned_data.get('text'))


_BLOG_INPUT_CLASS = (
    "form-input enterprise-input w-full h-11 !ring-0 !ring-offset-0 bg-secondary "
    "border-border focus:border-border rounded-xl text-sm text-foreground px-5"
)


class CoachBlogPostForm(forms.ModelForm):
    """A coach submits a post that an administrator must approve first."""

    reading_time = forms.IntegerField(
        min_value=1,
        max_value=240,
        initial=5,
        label=_("زمان مطالعه (دقیقه)"),
        widget=forms.NumberInput(attrs={"class": _BLOG_INPUT_CLASS, "min": 1, "max": 240}),
    )

    class Meta:
        model = ArticleBlogModel
        fields = (
            "title",
            "language_kinds",
            "subcategory",
            "image",
            "reading_time",
            "article_excerpt",
            "article_description",
        )
        widgets = {
            "title": forms.TextInput(
                attrs={"class": _BLOG_INPUT_CLASS, "placeholder": _("عنوان پست")}
            ),
            "language_kinds": forms.Select(attrs={"class": _BLOG_INPUT_CLASS}),
            "subcategory": forms.Select(attrs={"class": _BLOG_INPUT_CLASS}),
            "image": forms.FileInput(
                attrs={
                    "class": _BLOG_INPUT_CLASS,
                    "accept": "image/jpeg,image/png,image/webp,image/gif",
                }
            ),
            "article_excerpt": forms.Textarea(
                attrs={
                    "class": _BLOG_INPUT_CLASS + " h-auto min-h-[110px] py-3",
                    "rows": 3,
                    "placeholder": _("خلاصه کوتاه برای نمایش در فهرست بلاگ"),
                }
            ),
            "article_description": forms.Textarea(
                attrs={
                    "class": _BLOG_INPUT_CLASS + " h-auto min-h-[260px] py-3",
                    "rows": 12,
                    "placeholder": _("متن کامل پست"),
                }
            ),
        }
        labels = {
            "title": _("عنوان"),
            "language_kinds": _("دسته‌بندی"),
            "subcategory": _("زیربخش"),
            "image": _("تصویر شاخص"),
            "article_excerpt": _("خلاصه"),
            "article_description": _("متن پست"),
        }

    def clean_article_description(self):
        value = sanitize_rich_text(self.cleaned_data.get("article_description"))
        if not value:
            raise ValidationError(_("متن پست نمی‌تواند خالی باشد."))
        return value
