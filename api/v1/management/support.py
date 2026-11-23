"""Shared staff API pagination, audit logging, and athlete scoping."""

from __future__ import annotations

import logging

from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404

from account.models import User
from account.services.admin_portal_service import AdminPortalService
from api.utils import parse_int


logger = logging.getLogger("api.audit")


admin_portal_service = AdminPortalService()


USER_LIST_PAGE_SIZE = 25


MAX_PAGE_SIZE = 100


def _audit(actor, action: str, *, object_type: str, object_id: int | None = None):
    # Avoid logging request bodies, file names, phone numbers, or credentials.
    logger.info(
        "privileged_api_action actor_id=%s action=%s object_type=%s object_id=%s",
        actor.pk,
        action,
        object_type,
        object_id or "-",
        extra={"actor_id": actor.pk, "action": action, "object_type": object_type, "object_id": object_id},
    )


def _page(request, queryset, serializer):
    page_size = parse_int(request.GET.get("page_size", USER_LIST_PAGE_SIZE), field="page_size")
    page_number = parse_int(request.GET.get("page", 1), field="page")
    if not 1 <= page_size <= MAX_PAGE_SIZE:
        raise ValueError("page_size must be between 1 and 100.")
    if page_number < 1:
        raise ValueError("page must be greater than zero.")
    page = Paginator(queryset, page_size).get_page(page_number)
    return {
        "items": [serializer(item) for item in page.object_list],
        "pagination": {
            "page": page.number,
            "page_size": page_size,
            "pages": page.paginator.num_pages,
            "total": page.paginator.count,
            "has_next": page.has_next(),
            "has_previous": page.has_previous(),
        },
    }


def _multipart_bool(value, *, field: str, default: bool = False) -> bool:
    if value is None or str(value).strip() == "":
        return default
    normalized = str(value).strip().lower()
    if normalized in {"true", "1"}:
        return True
    if normalized in {"false", "0"}:
        return False
    raise ValidationError({field: "Expected true or false."})


def _coach_athlete(request, athlete_id):
    return get_object_or_404(User, pk=athlete_id, coach=request.user, is_active=True, is_admin=False, is_coach=False)
