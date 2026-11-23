"""Shared library pagination, redirects, and services."""

from __future__ import annotations

from django.core.paginator import Paginator
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme

from account.services.gym_library_service import GymLibraryService
from account.services.gym_program_service import GymProgramService


gym_library_service = GymLibraryService()


gym_program_service = GymProgramService()


GYM_PAGE_SIZE = 20


GYM_PAGE_SIZES = (10, 15, 20)


def paginate_gym_queryset(request, queryset):
    """Return the requested gym page together with standard pagination context."""
    try:
        page_size = int(request.GET.get("page_size", GYM_PAGE_SIZE))
    except (TypeError, ValueError):
        page_size = GYM_PAGE_SIZE
    if page_size not in GYM_PAGE_SIZES:
        page_size = GYM_PAGE_SIZE
    paginator = Paginator(queryset, page_size)
    page_obj = paginator.get_page(request.GET.get("page"))
    return {
        "paginator": paginator,
        "page_obj": page_obj,
        "is_paginated": page_obj.has_other_pages(),
        "page_size": page_size,
        "page_sizes": GYM_PAGE_SIZES,
    }


def redirect_to_gym_page(request, fallback_route):
    return_to = request.POST.get("return_to", "")
    if return_to and url_has_allowed_host_and_scheme(
        return_to,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(return_to)
    return redirect(fallback_route)
