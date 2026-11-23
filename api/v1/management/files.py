"""Staff and athlete APIs for private documents and media."""

from __future__ import annotations

import mimetypes
import os

from django.core.exceptions import ValidationError
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.utils.http import content_disposition_header

from account.admin_forms import AdminClientDocumentForm, AdminClientMediaForm
from account.models import ClientDocument, ClientDocumentPayment, ClientMedia, User
from account.services.client_file_service import open_client_file
from api.decorators import api_auth_required, api_endpoint, api_methods
from api.limits import rate_limit
from api.responses import error, ok
from api.utils import json_value
from api.v1.permissions import api_admin_required, api_coach_required

from .support import admin_portal_service, _audit, _multipart_bool, _coach_athlete


def _serialize_document(request, document, download_path=None):
    is_paid = not document.requires_payment or any(
        payment.user_id == document.user_id and payment.status == "paid" for payment in document.payments.all()
    )
    return {
        "id": document.pk,
        "title": document.title,
        "uploaded_at": json_value(document.uploaded_at),
        "uploaded_by": document.uploaded_by.fullname if document.uploaded_by_id else None,
        "requires_payment": document.requires_payment,
        "price": document.price,
        "paid": is_paid,
        "download_path": download_path or f"/api/v1/me/documents/{document.pk}/download/",
    }


def _serialize_media(request, media):
    return {
        "id": media.pk,
        "uploaded_at": json_value(media.uploaded_at),
        "uploaded_by": media.uploaded_by.fullname if media.uploaded_by_id else None,
        "image": f"/api/v1/me/media/{media.pk}/image/" if media.image else None,
        "video": f"/api/v1/me/media/{media.pk}/video/" if media.video else None,
    }


def _document_collection(request, user, *, allow_post=False, management_path=None):
    if request.method == "GET":
        _audit(request.user, "client_files.list.read", object_type="user", object_id=user.pk)
        documents = ClientDocument.objects.filter(user=user).select_related("uploaded_by").prefetch_related("payments")
        media = ClientMedia.objects.filter(user=user).select_related("uploaded_by")
        return ok({
            "documents": [_serialize_document(request, item, f"{management_path}/{item.pk}/download/" if management_path else None) for item in documents],
            "media": [_serialize_media(request, item) for item in media],
        })

    if not allow_post:
        return error("method_not_allowed", "This HTTP method is not supported.", status=405)
    upload_kind = str(request.POST.get("upload_kind") or "document").strip()
    if upload_kind == "document":
        allowed_fields = {"upload_kind", "title", "price", "requires_payment"}
        if set(request.POST) - allowed_fields or set(request.FILES) - {"file"}:
            raise ValidationError({"fields": "Unsupported document upload fields."})
        form_data = request.POST.copy()
        requires_payment = _multipart_bool(request.POST.get("requires_payment"), field="requires_payment")
        form_data.setlist("access_type", ["unpaid" if requires_payment else "paid"])
        form = AdminClientDocumentForm(data=form_data, files=request.FILES)
        if not form.is_valid():
            raise ValidationError(form.errors)
        item = admin_portal_service.save_document(user=user, uploaded_by=request.user, form=form)
        _audit(request.user, "document.upload", object_type="client_document", object_id=item.pk)
        path = f"{management_path}/{item.pk}/download/" if management_path else None
        return ok({"document": _serialize_document(request, item, path)}, status=201)
    if upload_kind == "media":
        if set(request.POST) - {"upload_kind"} or set(request.FILES) - {"image", "video"}:
            raise ValidationError({"fields": "Unsupported media upload fields."})
        form = AdminClientMediaForm(data=request.POST, files=request.FILES)
        if not form.is_valid():
            raise ValidationError(form.errors)
        if not form.cleaned_data.get("image") and not form.cleaned_data.get("video"):
            raise ValidationError({"file": "At least one image or video is required."})
        item = admin_portal_service.save_media(user=user, uploaded_by=request.user, form=form)
        _audit(request.user, "media.upload", object_type="client_media", object_id=item.pk)
        return ok({"media": _serialize_media(request, item)}, status=201)
    raise ValidationError({"upload_kind": "upload_kind must be document or media."})


@api_endpoint
@api_methods("GET")
@api_auth_required
def documents(request):
    return _document_collection(request, request.user)


def _serve_document(request, document, *, may_manage=False):
    if not may_manage and document.user_id != request.user.pk:
        return error("not_found", "The requested resource was not found.", status=404)
    if not may_manage and document.requires_payment and not any(
        payment.user_id == request.user.pk and payment.status == ClientDocumentPayment.Status.PAID
        for payment in document.payments.all()
    ):
        return error("payment_required", "This document requires payment.", status=403)
    try:
        file = open_client_file(document.file)
    except FileNotFoundError:
        return error("file_unavailable", "The document file is unavailable.", status=404)
    _audit(request.user, "client_document.download", object_type="client_document", object_id=document.pk)
    response = FileResponse(file, as_attachment=True, filename=os.path.basename(document.file.name))
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@api_endpoint
@api_methods("GET")
@api_auth_required
def client_media_download(request, media_id, kind):
    media = get_object_or_404(ClientMedia, pk=media_id)
    allowed = media.user_id == request.user.pk or request.user.is_admin or (
        request.user.is_coach and media.user.coach_id == request.user.pk
    )
    if not allowed:
        return error("not_found", "The requested resource was not found.", status=404)
    field = media.image if kind == "image" else media.video if kind == "video" else None
    if field is None or not field:
        return error("not_found", "The requested resource was not found.", status=404)
    try:
        file = open_client_file(field)
    except FileNotFoundError:
        return error("file_unavailable", "The media file is unavailable.", status=404)
    _audit(request.user, "client_media.download", object_type="client_media", object_id=media.pk)
    content_type = mimetypes.guess_type(field.name)[0] or "application/octet-stream"
    response = FileResponse(file, content_type=content_type)
    response["Content-Disposition"] = content_disposition_header(False, os.path.basename(field.name))
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@api_endpoint
@api_methods("GET")
@api_auth_required
def client_document_download(request, document_id):
    document = get_object_or_404(ClientDocument.objects.prefetch_related("payments"), pk=document_id, user=request.user)
    return _serve_document(request, document)


@api_endpoint
@api_methods("GET")
@api_auth_required
@api_coach_required
def coach_document_download(request, athlete_id, document_id):
    athlete = _coach_athlete(request, athlete_id)
    document = get_object_or_404(ClientDocument, pk=document_id, user=athlete)
    return _serve_document(request, document, may_manage=True)


@api_endpoint
@api_methods("GET")
@api_auth_required
@api_admin_required
def admin_document_download(request, user_id, document_id):
    user = get_object_or_404(User, pk=user_id, is_admin=False)
    document = get_object_or_404(ClientDocument, pk=document_id, user=user)
    return _serve_document(request, document, may_manage=True)


@api_endpoint
@api_methods("GET", "POST")
@api_auth_required
@api_coach_required
@rate_limit(name="coach-client-upload", limit=20, window=3600)
def coach_athlete_documents(request, athlete_id):
    athlete = _coach_athlete(request, athlete_id)
    return _document_collection(request, athlete, allow_post=True, management_path=f"/api/v1/coach/athletes/{athlete.pk}/documents")


@api_endpoint
@api_methods("GET", "POST")
@api_auth_required
@api_admin_required
@rate_limit(name="admin-client-upload", limit=50, window=3600)
def admin_user_documents(request, user_id):
    user = get_object_or_404(User, pk=user_id, is_admin=False)
    return _document_collection(request, user, allow_post=True, management_path=f"/api/v1/admin/users/{user.pk}/documents")
