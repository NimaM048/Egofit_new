"""Staff and coach APIs for account and roster management."""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404

from account.froms import CoachProfileForm
from account.models import User
from account.validators import validate_strong_password
from api.auth import revoke_user_tokens
from api.decorators import api_auth_required, api_endpoint, api_methods
from api.limits import rate_limit
from api.responses import error, ok
from api.utils import json_value, parse_int, request_data
from api.v1.permissions import api_admin_required, api_coach_required

from .support import _audit, _page


def _serialize_user(user):
    return {
        "id": user.pk,
        "fullname": user.fullname,
        "display_name": user.display_name,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "phone": user.phone,
        "email": user.email,
        "is_active": user.is_active,
        "is_admin": user.is_admin,
        "is_coach": user.is_coach,
        "coach": {"id": user.coach_id, "fullname": user.coach.fullname} if user.coach_id else None,
        "created_at": json_value(user.date_joined) if hasattr(user, "date_joined") else None,
    }


@api_endpoint
@api_methods("GET")
@api_auth_required
@api_admin_required
def admin_users(request):
    queryset = User.objects.select_related("coach").order_by("fullname", "id")
    query = (request.GET.get("q") or "").strip()[:80]
    if query:
        queryset = queryset.filter(Q(fullname__icontains=query) | Q(phone__icontains=query) | Q(email__icontains=query))
    role = request.GET.get("role")
    if role == "coach":
        queryset = queryset.filter(is_coach=True)
    elif role == "admin":
        queryset = queryset.filter(is_admin=True)
    elif role == "user":
        queryset = queryset.filter(is_admin=False, is_coach=False)
    return ok(_page(request, queryset, _serialize_user))


@api_endpoint
@api_methods("PATCH")
@api_auth_required
@api_admin_required
def admin_coach_role(request, user_id):
    data = request_data(request)
    if set(data) != {"is_coach"} or not isinstance(data.get("is_coach"), bool):
        raise ValidationError({"is_coach": "A boolean is_coach field is required."})
    with transaction.atomic():
        # Coach and administrator are independent capabilities: an existing
        # administrator may also take the coach roster (the seeded owner account
        # is exactly this dual role), so only self-changes are blocked here.
        target = get_object_or_404(User.objects.select_for_update(), pk=user_id)
        if target.pk == request.user.pk:
            return error("self_role_change_denied", "You cannot change your own coach role.", status=409)
        target.is_coach = data["is_coach"]
        target.save(update_fields=["is_coach"])
        if not target.is_coach:
            User.objects.filter(coach=target).update(coach=None)
            revoke_user_tokens(target)
    _audit(request.user, "coach_role.update", object_type="user", object_id=target.pk)
    return ok({"user": _serialize_user(target)})


@api_endpoint
@api_methods("PATCH")
@api_auth_required
@api_admin_required
def admin_assign_coach(request, user_id):
    data = request_data(request)
    if set(data) != {"coach_id"}:
        raise ValidationError({"coach_id": "coach_id is required; use null to unassign."})
    with transaction.atomic():
        target = get_object_or_404(
            User.objects.select_for_update(), pk=user_id, is_admin=False, is_coach=False
        )
        coach = None
        if data["coach_id"] is not None:
            coach_id = parse_int(data["coach_id"], field="coach_id")
            coach = get_object_or_404(User.objects.select_for_update(), pk=coach_id, is_coach=True, is_active=True)
            if coach.pk == target.pk:
                raise ValidationError({"coach_id": "A user cannot be their own coach."})
        target.coach = coach
        target.save(update_fields=["coach"])
    _audit(request.user, "coach_assignment.update", object_type="user", object_id=target.pk)
    return ok({"user_id": target.pk, "coach": {"id": coach.pk, "fullname": coach.fullname} if coach else None})


@api_endpoint
@api_methods("POST")
@api_auth_required
@api_admin_required
@rate_limit(name="admin-password-reset", limit=10, window=3600)
def admin_reset_password(request, user_id):
    data = request_data(request)
    if set(data) != {"new_password"}:
        raise ValidationError({"new_password": "Only new_password is accepted."})
    validate_strong_password(str(data.get("new_password") or ""))
    with transaction.atomic():
        target = get_object_or_404(User.objects.select_for_update(), pk=user_id)
        target.set_password(data["new_password"])
        target.save(update_fields=["password"])
        revoke_user_tokens(target)
    _audit(request.user, "user.password_reset", object_type="user", object_id=target.pk)
    return ok({"changed": True, "user_id": target.pk, "sessions_revoked": True})


@api_endpoint
@api_methods("GET")
@api_auth_required
@api_coach_required
def coach_athletes(request):
    queryset = User.objects.filter(coach=request.user, is_active=True, is_admin=False, is_coach=False).select_related("coach").order_by("fullname")
    query = (request.GET.get("q") or "").strip()[:80]
    if query:
        queryset = queryset.filter(
            Q(fullname__icontains=query) | Q(phone__icontains=query) | Q(email__icontains=query)
        )
    return ok(_page(request, queryset, _serialize_user))


@api_endpoint
@api_methods("PATCH")
@api_auth_required
@api_admin_required
def admin_user_role(request, user_id):
    """Set admin / coach / athlete for a user in one audited call."""
    data = request_data(request)
    if set(data) != {"role"}:
        raise ValidationError({"role": "A role field is required."})
    role = str(data.get("role") or "").strip()
    if role not in User.Role.values:
        raise ValidationError({"role": "role must be admin, coach or athlete."})

    with transaction.atomic():
        target = get_object_or_404(User.objects.select_for_update(), pk=user_id)
        if target.pk == request.user.pk and role != User.Role.ADMIN:
            return error("self_role_change_denied", "You cannot remove your own admin role.", status=409)
        previous_role = target.role
        target.set_role(role)
        target.save(update_fields=["is_admin", "is_coach"])
        if role == User.Role.ATHLETE:
            User.objects.filter(coach=target).update(coach=None)
        if previous_role != role:
            revoke_user_tokens(target)
    _audit(request.user, "user.role.update", object_type="user", object_id=target.pk)
    return ok({"user": _serialize_user(target), "role": target.role})


@api_endpoint
@api_methods("PATCH")
@api_auth_required
@api_coach_required
def coach_profile(request):
    """Coaches update their biography, avatar and contact number."""
    form = CoachProfileForm(request.POST, request.FILES, instance=request.user)
    if not form.is_valid():
        raise ValidationError(form.errors.get_json_data())
    form.save()
    _audit(request.user, "coach.profile.update", object_type="user", object_id=request.user.pk)
    return ok({"user": _serialize_user(request.user)})
