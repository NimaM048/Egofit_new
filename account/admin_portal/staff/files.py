"""Staff pages for documents and athlete media."""

from __future__ import annotations

import logging

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import TemplateView

from account.admin_forms import AdminClientDocumentForm, AdminClientMediaForm
from account.models import ClientDocument, ClientMedia

from .mixins import StaffUserMixin
from .services import admin_portal_service


class AdminUserFilesView(StaffUserMixin, TemplateView):
    template_name = "admin_portal/user_files.html"
    active_section = "records"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "client_documents": self.target_user.client_documents.select_related("uploaded_by").all(),
                "client_media": self.target_user.client_media.select_related("uploaded_by").all(),
                "page_title": _("فایل‌های کاربر"),
            }
        )
        return context


class AdminUserFileDeleteView(StaffUserMixin, View):
    def post(self, request, user_id, file_kind, pk):
        if file_kind == "document":
            instance = get_object_or_404(ClientDocument, pk=pk, user=self.target_user)
            admin_portal_service.delete_client_document(document=instance)
        elif file_kind == "media":
            instance = get_object_or_404(ClientMedia, pk=pk, user=self.target_user)
            admin_portal_service.delete_client_media(media=instance)
        else:
            return JsonResponse({"detail": "نوع فایل معتبر نیست."}, status=404)
        messages.success(request, _("فایل با موفقیت حذف شد."))
        return redirect("register:admin_user_files", user_id=self.target_user.pk)


class AdminUploadView(StaffUserMixin, View):
    template_name = "admin_portal/upload.html"
    active_section = "search"
    hide_admin_chrome = True

    def get(self, request, user_id):
        return render(request, self.template_name, self._context(AdminClientMediaForm(), AdminClientDocumentForm()))

    def post(self, request, user_id):
        upload_kind = request.POST.get("upload_kind")
        document_form = AdminClientDocumentForm()
        media_form = AdminClientMediaForm()

        if upload_kind == "document":
            document_form = AdminClientDocumentForm(request.POST, request.FILES)
            form = document_form
            save_file = admin_portal_service.save_document
        elif upload_kind in (None, "media"):
            media_form = AdminClientMediaForm(request.POST, request.FILES)
            form = media_form
            save_file = admin_portal_service.save_media
        else:
            messages.error(request, _("نوع ارسال فایل مشخص نیست. از یکی از فرم‌های همین صفحه استفاده کنید."))
            return render(request, self.template_name, self._context(media_form, document_form), status=400)

        status = 200
        if form.is_valid():
            if form is media_form and not (form.cleaned_data.get("image") or form.cleaned_data.get("video")):
                form.add_error(None, _("حداقل یک فایل تصویر یا ویدیو انتخاب کنید."))
            else:
                try:
                    save_file(user=self.target_user, uploaded_by=request.user, form=form)
                except OSError:
                    logging.getLogger(__name__).exception("Client upload I/O failed kind=%s", upload_kind)
                    form.add_error(None, _("در ذخیرهٔ فایل مشکلی پیش آمد. فایل را دوباره انتخاب و ارسال کنید."))
                    status = 503
                else:
                    messages.success(request, _("فایل با موفقیت بارگذاری شد."))
                    if form is document_form:
                        return redirect("register:admin_user_hub", user_id=self.target_user.pk)
                    return redirect("register:admin_search")

        return render(request, self.template_name, self._context(media_form, document_form), status=status)

    def _context(self, media_form, document_form):
        return {
            "form": media_form,
            "document_form": document_form,
            "target_user": self.target_user,
            "active_section": self.active_section,
            "hide_admin_chrome": self.hide_admin_chrome,
            "page_title": _("آپلود فایل"),
        }
