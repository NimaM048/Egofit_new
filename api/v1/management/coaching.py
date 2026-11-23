"""Coach requests, applications, exercise suggestions, and review queues."""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404

from account.froms import CoachApplicationForm, ExerciseSuggestionForm
from account.models import (
    BodyCircumferenceMeasurement,
    CaliperMeasurement,
    CoachApplication,
    CoachRequest,
    ExerciseSuggestion,
    User,
)
from account.services.admin_financial_analytics_service import AdminFinancialAnalyticsService
from account.services.coach_application_service import CoachApplicationService
from account.services.exercise_suggestion_service import ExerciseSuggestionService
from api.decorators import api_auth_required, api_endpoint, api_methods
from api.limits import rate_limit
from api.responses import error, ok
from api.utils import absolute_file_url, json_value, request_data
from api.v1.permissions import api_admin_required, api_coach_required

from .support import _audit, _page


coach_application_service = CoachApplicationService()


exercise_suggestion_service = ExerciseSuggestionService()


def _serialize_coach_request(item):
    return {
        "id": item.pk,
        "athlete": {
            "id": item.user_id,
            "fullname": item.user.fullname,
            "phone": item.user.phone,
        },
        "status": item.status,
        "sessions_per_week": item.sessions_per_week,
        "wants_diet": item.wants_diet,
        "wants_workout": item.wants_workout,
        "created_at": json_value(item.created_at),
        "updated_at": json_value(item.updated_at),
    }


@api_endpoint
@api_methods("GET")
@api_auth_required
@api_coach_required
def coach_athlete_requests(request):
    """Program requests from the coach's own athletes only."""
    queryset = (
        CoachRequest.objects.filter(user__coach=request.user)
        .select_related("user")
        .order_by("status", "-created_at")
    )
    status = (request.GET.get("status") or "").strip()
    if status in CoachRequest.Status.values:
        queryset = queryset.filter(status=status)
    return ok({"items": [_serialize_coach_request(item) for item in queryset[:100]]})


@api_endpoint
@api_methods("PATCH")
@api_auth_required
@api_coach_required
def coach_athlete_request_handled(request, request_id):
    data = request_data(request)
    if set(data) - {"status"}:
        raise ValidationError({"detail": "Only status is accepted."})
    if data.get("status") not in {None, "", CoachRequest.Status.HANDLED}:
        raise ValidationError({"status": "status must be handled."})
    with transaction.atomic():
        item = get_object_or_404(
            CoachRequest.objects.select_for_update().select_related("user"),
            pk=request_id,
            user__coach=request.user,
        )
        item.status = CoachRequest.Status.HANDLED
        item.save(update_fields=["status", "updated_at"])
    _audit(
        request.user,
        "coach.athlete_request.handle",
        object_type="coach_request",
        object_id=item.pk,
    )
    return ok({"request": _serialize_coach_request(item)})


def _serialize_coach_application(request, application):
    return {
        "id": application.pk,
        "user_id": application.user_id,
        "full_name": application.full_name,
        "phone": application.phone,
        "biography": application.biography,
        "resume_url": absolute_file_url(request, application.resume) if application.resume else None,
        "photo_url": absolute_file_url(request, application.photo) if application.photo else None,
        "status": application.status,
        "rejection_reason": application.rejection_reason,
        "reviewed_at": json_value(application.reviewed_at),
        "created_at": json_value(application.created_at),
    }


def _serialize_exercise_suggestion(request, suggestion):
    return {
        "id": suggestion.pk,
        "requested_by": {"id": suggestion.requested_by_id, "fullname": suggestion.requested_by.fullname},
        "kind": suggestion.kind,
        "name_fa": suggestion.name_fa,
        "name_en": suggestion.name_en,
        "goal": suggestion.goal,
        "image_url": absolute_file_url(request, suggestion.image) if suggestion.image else None,
        "status": suggestion.status,
        "rejection_reason": suggestion.rejection_reason,
        "reviewed_at": json_value(suggestion.reviewed_at),
        "created_at": json_value(suggestion.created_at),
    }


@api_endpoint
@api_methods("GET", "POST")
@api_auth_required
def me_coach_application(request):
    """Athlete submits (or refreshes) a request to become a coach."""
    if request.method == "GET":
        application = coach_application_service.get_latest_for_user(request.user)
        return ok(
            {
                "role": request.user.role,
                "application": _serialize_coach_application(request, application)
                if application
                else None,
            }
        )

    if request.user.is_admin or request.user.is_coach:
        return error("role_conflict", "This account already has coach or admin access.", status=409)

    form = CoachApplicationForm(request.POST, request.FILES)
    if not form.is_valid():
        raise ValidationError(form.errors.get_json_data())
    application = coach_application_service.submit(user=request.user, form=form)
    _audit(request.user, "coach_application.submit", object_type="coach_application", object_id=application.pk)
    return ok({"application": _serialize_coach_application(request, application)}, status=201)


@api_endpoint
@api_methods("GET")
@api_auth_required
@api_admin_required
def admin_coach_applications(request):
    queryset = coach_application_service.get_all_queryset()
    status = (request.GET.get("status") or "").strip()
    if status in CoachApplication.Status.values:
        queryset = queryset.filter(status=status)
    return ok(_page(request, queryset, lambda item: _serialize_coach_application(request, item)))


@api_endpoint
@api_methods("PATCH")
@api_auth_required
@api_admin_required
def admin_coach_application_review(request, application_id):
    data = request_data(request)
    if set(data) - {"action", "reason"}:
        raise ValidationError({"detail": "Only action and reason are accepted."})
    action = str(data.get("action") or "").strip()
    reason = str(data.get("reason") or "").strip()
    if action not in {"approve", "reject"}:
        raise ValidationError({"action": "action must be approve or reject."})
    if action == "reject" and not reason:
        raise ValidationError({"reason": "A rejection reason is required."})

    with transaction.atomic():
        application = get_object_or_404(
            CoachApplication.objects.select_for_update().select_related("user"),
            pk=application_id,
        )
        if application.status != CoachApplication.Status.PENDING:
            return error("already_reviewed", "This application has already been reviewed.", status=409)
        if action == "approve":
            coach_application_service.approve(application=application, actor=request.user)
        else:
            coach_application_service.reject(
                application=application, actor=request.user, reason=reason
            )
    _audit(
        request.user,
        f"coach_application.{action}",
        object_type="coach_application",
        object_id=application.pk,
    )
    return ok({"application": _serialize_coach_application(request, application)})


@api_endpoint
@api_methods("GET", "POST")
@api_auth_required
@api_coach_required
@rate_limit(name="coach-exercise-suggestion", limit=20, window=3600)
def coach_exercise_suggestions(request):
    if request.method == "GET":
        queryset = exercise_suggestion_service.get_for_coach(request.user)
        return ok(
            {
                "items": [
                    _serialize_exercise_suggestion(request, item) for item in queryset[:50]
                ]
            }
        )

    form = ExerciseSuggestionForm(request.POST, request.FILES)
    if not form.is_valid():
        raise ValidationError(form.errors.get_json_data())
    suggestion = exercise_suggestion_service.submit(coach=request.user, form=form)
    _audit(
        request.user,
        "coach.exercise_suggestion.submit",
        object_type="exercise_suggestion",
        object_id=suggestion.pk,
    )
    return ok({"suggestion": _serialize_exercise_suggestion(request, suggestion)}, status=201)


@api_endpoint
@api_methods("GET")
@api_auth_required
@api_admin_required
def admin_exercise_suggestions(request):
    queryset = exercise_suggestion_service.get_all_queryset()
    status = (request.GET.get("status") or "").strip()
    if status in ExerciseSuggestion.Status.values:
        queryset = queryset.filter(status=status)
    return ok(_page(request, queryset, lambda item: _serialize_exercise_suggestion(request, item)))


@api_endpoint
@api_methods("PATCH")
@api_auth_required
@api_admin_required
def admin_exercise_suggestion_review(request, suggestion_id):
    data = request_data(request)
    if set(data) - {"action", "reason"}:
        raise ValidationError({"detail": "Only action and reason are accepted."})
    action = str(data.get("action") or "").strip()
    reason = str(data.get("reason") or "").strip()
    if action not in {"approve", "reject"}:
        raise ValidationError({"action": "action must be approve or reject."})
    if action == "reject" and not reason:
        raise ValidationError({"reason": "A rejection reason is required."})

    with transaction.atomic():
        suggestion = get_object_or_404(
            ExerciseSuggestion.objects.select_for_update().select_related("requested_by"),
            pk=suggestion_id,
        )
        if suggestion.status != ExerciseSuggestion.Status.PENDING:
            return error("already_reviewed", "This suggestion has already been reviewed.", status=409)
        if action == "approve":
            exercise_suggestion_service.approve(suggestion=suggestion, actor=request.user)
        else:
            exercise_suggestion_service.reject(
                suggestion=suggestion, actor=request.user, reason=reason
            )
    _audit(
        request.user,
        f"exercise_suggestion.{action}",
        object_type="exercise_suggestion",
        object_id=suggestion.pk,
    )
    return ok({"suggestion": _serialize_exercise_suggestion(request, suggestion)})


@api_endpoint
@api_methods("GET")
@api_auth_required
@api_admin_required
def admin_analytics_overview(request):
    athletes = User.objects.filter(is_admin=False, is_coach=False, is_active=True)
    coaches = User.objects.filter(is_coach=True, is_active=True)
    financial_analytics = AdminFinancialAnalyticsService.dashboard_data(
        request.GET.get("from_date"),
        request.GET.get("to_date"),
    )
    return ok(
        {
            "totals": {
                "athletes": athletes.count(),
                "coaches": coaches.count(),
                "admins": User.objects.filter(is_admin=True, is_active=True).count(),
                "assigned_athletes": athletes.filter(coach__isnull=False).count(),
                "unassigned_athletes": athletes.filter(coach__isnull=True).count(),
                "circumference_measurements": BodyCircumferenceMeasurement.objects.count(),
                "caliper_measurements": CaliperMeasurement.objects.count(),
                "pending_coach_applications": coach_application_service.pending_count(),
                "pending_exercise_suggestions": exercise_suggestion_service.pending_count(),
            },
            "coaches": [
                {
                    "id": coach.pk,
                    "fullname": coach.fullname,
                    "athlete_count": coach.athlete_count,
                }
                for coach in coaches.annotate(athlete_count=Count("athletes")).order_by(
                    "-athlete_count", "fullname"
                )
            ],
            "financial": financial_analytics,
        }
    )
