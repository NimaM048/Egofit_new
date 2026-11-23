"""Role and athlete scope shared by staff pages."""

from __future__ import annotations

from django.shortcuts import get_object_or_404

from account.models import User
from account.portal_mixins import (
    AdminRequiredMixin,
    StaffRequiredMixin,
    is_coach_only,
    staff_portal_role,
)


class AdminPageMixin(AdminRequiredMixin):
    active_section = "search"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("active_section", self.active_section)
        context.setdefault("portal_role", "admin")
        context.setdefault("is_coach_only", False)
        return context


class StaffPageMixin(StaffRequiredMixin):
    """Shared chrome for pages both administrators and coaches may open.

    ``portal_role`` lets the templates hide administrator-only navigation and
    actions while keeping the same look and layout for coaches.
    """

    active_section = "search"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("active_section", self.active_section)
        context["portal_role"] = staff_portal_role(self.request.user)
        context["is_coach_only"] = is_coach_only(self.request.user)
        return context


class StaffUserMixin(StaffPageMixin):
    """Resolve ``target_user`` and forbid coaches from opening other athletes.

    A pure coach may only open the record of an athlete assigned to them; an
    administrator keeps unrestricted access.
    """

    def dispatch(self, request, *args, **kwargs):
        if is_coach_only(request.user):
            self.target_user = get_object_or_404(
                User,
                pk=kwargs["user_id"],
                coach=request.user,
                is_active=True,
                is_admin=False,
                is_coach=False,
            )
        else:
            self.target_user = get_object_or_404(User, pk=kwargs["user_id"])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["target_user"] = self.target_user
        return context


class AdminUserMixin(AdminPageMixin):
    def dispatch(self, request, *args, **kwargs):
        self.target_user = get_object_or_404(User, pk=kwargs["user_id"])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["target_user"] = self.target_user
        return context
