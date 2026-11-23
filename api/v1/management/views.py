from __future__ import annotations

import logging
import mimetypes
import os
import json

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import FileResponse
from django.http import QueryDict
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.http import content_disposition_header

from account.admin_forms import (
    AdminCaliperForm,
    AdminCircumferenceForm,
    AdminClientDocumentForm,
    AdminClientMediaForm,
    AdminHealthRecordForm,
    AutomaticProgrammingForm,
    WorkoutProgramForm,
)
from account.froms import (
    CoachApplicationForm,
    CoachProfileForm,
    ExerciseSuggestionForm,
)
from account.models import (
    AthleteTrainingProfile,
    BodyCircumferenceMeasurement,
    CaliperMeasurement,
    ClientDocument,
    ClientDocumentPayment,
    ClientMedia,
    CoachApplication,
    CoachRequest,
    ExerciseSuggestion,
    User,
    UserHealthRecord,
    WorkoutPerformanceRecord,
    WorkoutProgramPayment,
    TrainingAdjustmentRecommendation,
)
from account.services.admin_portal_service import AdminPortalService
from account.services.admin_financial_analytics_service import AdminFinancialAnalyticsService
from account.services.coach_application_service import CoachApplicationService
from account.services.client_file_service import open_client_file
from account.services.exercise_suggestion_service import ExerciseSuggestionService
from account.services.gym_program_service import GymProgramService
from account.services.automatic_program_service import AutomaticProgramService
from account.services.exercise_preference_service import ExercisePreferenceService
from account.services.program_template_service import ProgramTemplateService
from account.services.training_recommendation_review_service import TrainingRecommendationReviewService
from account.validators import validate_image_upload, validate_strong_password, validate_video_upload
from api.auth import revoke_user_tokens
from api.decorators import api_auth_required, api_endpoint, api_methods
from api.limits import rate_limit
from api.responses import error, ok
from api.utils import absolute_file_url, json_value, parse_int, request_data
from api.v1.permissions import api_admin_required, api_coach_required
from cart.models import Order
from home.models import ArticleBlogModel, Category, Episode, Season, SeriesModel
from home.services.content_service import invalidate_blog_caches
from home.constants import HOME_PAGE_CACHE_PREFIX
from home.selectors.course_selector import SERIES_COUNT_CACHE_KEY


logger = logging.getLogger("api.audit")
admin_portal_service = AdminPortalService()
gym_program_service = GymProgramService()
automatic_program_service = AutomaticProgramService()
coach_application_service = CoachApplicationService()
exercise_suggestion_service = ExerciseSuggestionService()

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


def _parse_jalali_range(raw_start, raw_end):
    """Parse optional Jalali ``YYYY/MM/DD`` bounds into Gregorian dates.

    Coaches and administrators asked to narrow an athlete's history to a window
    such as ``1404/10/12`` to ``1405/8/15``. Measurement rows are filtered by
    their Gregorian ``measured_at`` timestamp. Invalid or missing values
    are ignored rather than raising so a stray query string never breaks the
    screen.
    """
    import jdatetime

    def _one(value):
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        # Mobile numeric keypads cannot always emit "/", so an 8-digit value
        # such as "14041012" is treated as "1404/10/12".
        digits = "".join(ch for ch in text if ch.isdigit())
        if "/" not in text and len(digits) == 8:
            text = f"{digits[:4]}/{digits[4:6]}/{digits[6:]}"
        try:
            parts = [int(part) for part in text.split("/")]
            if len(parts) != 3:
                return None
            return jdatetime.date(parts[0], parts[1], parts[2]).togregorian()
        except (ValueError, TypeError):
            return None

    start = _one(raw_start)
    end = _one(raw_end)
    if start and end and start > end:
        start, end = end, start
    return start, end


def _local_window(start, end):
    """Turn inclusive local calendar dates into an aware half-open datetime range.

    ``measured_at`` is a timezone-aware datetime. Filtering with ``__date``
    relies on the database's timezone tables (``CONVERT_TZ``), which are not
    guaranteed to be loaded on MySQL, so a local-midnight datetime window is
    used instead. The end bound is exclusive (start of the following day).
    """
    from datetime import datetime, time, timedelta

    lower = None
    upper = None
    if start:
        lower = timezone.make_aware(datetime.combine(start, time.min), timezone.get_current_timezone())
    if end:
        exclusive = end + timedelta(days=1)
        upper = timezone.make_aware(datetime.combine(exclusive, time.min), timezone.get_current_timezone())
    return lower, upper


def _filter_measurement_queryset(queryset, *, start, end):
    """Restrict a measurement queryset to the requested Gregorian window."""
    lower, upper = _local_window(start, end)
    if lower:
        queryset = queryset.filter(measured_at__gte=lower)
    if upper:
        queryset = queryset.filter(measured_at__lt=upper)
    return queryset


def _analysis_payload(user, *, start=None, end=None, coach_scope=False):
    range_start, range_end = _parse_jalali_range(start, end)
    hidden_measurement_fields = {"id", "user", "legacy_id", "recorded_at", "measured_at_jalali"}
    circumference_fields = [field.name for field in BodyCircumferenceMeasurement._meta.fields if field.name not in hidden_measurement_fields]
    caliper_fields = [field.name for field in CaliperMeasurement._meta.fields if field.name not in hidden_measurement_fields]
    circumference_queryset = _filter_measurement_queryset(
        BodyCircumferenceMeasurement.objects.filter(user=user), start=range_start, end=range_end
    )
    caliper_queryset = _filter_measurement_queryset(
        CaliperMeasurement.objects.filter(user=user), start=range_start, end=range_end
    )
    health_queryset = UserHealthRecord.objects.filter(user=user)
    performance_queryset = WorkoutPerformanceRecord.objects.filter(user=user)
    if range_start:
        health_queryset = health_queryset.filter(recorded_at__gte=range_start)
        performance_queryset = performance_queryset.filter(recorded_on__gte=range_start)
    if range_end:
        health_queryset = health_queryset.filter(recorded_at__lte=range_end)
        performance_queryset = performance_queryset.filter(recorded_on__lte=range_end)
    if coach_scope:
        health_queryset = health_queryset.filter(
            category__in=(UserHealthRecord.CategoryChoices.INJURY, UserHealthRecord.CategoryChoices.ABNORMALITY),
            status=UserHealthRecord.Status.ACTIVE,
        ).filter(Q(training_restriction=True) | Q(body_region__gt=""))
    health_payload = []
    for item in health_queryset[:100]:
        if coach_scope:
            health_payload.append({
                "id": item.pk,
                "category": item.category,
                "body_region": item.body_region,
                "affected_side": item.affected_side,
                "severity": item.severity,
                "onset_date": json_value(item.onset_date),
                "status": item.status,
                "resolved_date": json_value(item.resolved_date),
                "training_restriction": item.training_restriction,
                "training_restriction_notes": item.training_restriction_notes,
            })
        else:
            health_payload.append({
                "id": item.pk,
                "category": item.category,
                "title": item.title,
                "details": item.details,
                "recorded_at": json_value(item.recorded_at),
                "body_region": item.body_region,
                "affected_side": item.affected_side,
                "severity": item.severity,
                "onset_date": json_value(item.onset_date),
                "status": item.status,
                "resolved_date": json_value(item.resolved_date),
                "training_restriction": item.training_restriction,
                "training_restriction_notes": item.training_restriction_notes,
            })
    return {
        "profile": {
            "weight_kg": json_value(user.weight_kg),
            "height_cm": json_value(user.height_cm),
            "birth_date_jalali": user.birth_date_jalali,
            "gender": user.gender,
            "activity_level": user.activity_level,
        },
        "range": {
            "start": start if range_start else None,
            "end": end if range_end else None,
        },
        "circumference_measurements": [
            {"id": row.pk, **{key: json_value(getattr(row, key)) for key in circumference_fields}}
            for row in circumference_queryset[:100]
        ],
        "caliper_measurements": [
            {"id": row.pk, **{key: json_value(getattr(row, key)) for key in caliper_fields}}
            for row in caliper_queryset[:100]
        ],
        "health_records": health_payload,
        "workout_performance": list(
            performance_queryset
            .select_related("exercise", "program")
            .values(
                "id",
                "exercise_id",
                "exercise__name",
                "exercise_name_snapshot",
                "program_id",
                "program__title",
                "repetitions",
                "mode",
                "set_number",
                "value",
                "recorded_on",
                "updated_at",
            )[:200]
        ),
    }


def _create_program(request, target_user, *, coach_mode=False):
    data = request_data(request)
    allowed = {
        "title", "program_type", "start_date", "end_date", "notes", "supplements_note",
        "warmup_notes", "cooldown_notes", "is_published", "requires_payment", "price", "days", "correctives",
    }
    unknown = set(data) - allowed
    if unknown:
        raise ValidationError({"fields": f"Unsupported fields: {', '.join(sorted(unknown))}"})
    days = data.get("days", [])
    correctives = data.get("correctives", [])
    if not isinstance(days, list) or not isinstance(correctives, list):
        raise ValidationError({"days": "days and correctives must be arrays."})
    form_data = {key: data[key] for key in (
        "title", "program_type", "start_date", "end_date", "is_published", "requires_payment", "price",
        "notes", "supplements_note", "warmup_notes", "cooldown_notes",
    ) if key in data}
    # The mobile builders do not collect a start date; the model already defaults
    # to today, so mirror that here instead of failing DateField validation.
    if not form_data.get("start_date"):
        form_data["start_date"] = timezone.localdate()
    if coach_mode:
        # Coaches cannot sell prescriptions or create hidden/draft programs.
        form_data["requires_payment"] = False
        form_data["price"] = 0
        form_data["is_published"] = True
    form = WorkoutProgramForm(data=form_data)
    if not form.is_valid():
        raise ValidationError(form.errors)
    program = gym_program_service.save_program(
        program_form=form,
        target_user=target_user,
        prescribed_by=request.user,
        days_payload=days,
        correctives_payload=correctives,
    )
    _audit(request.user, "workout_program.create", object_type="workout_program", object_id=program.pk)
    return program


@api_endpoint
@api_methods("POST")
@api_auth_required
@rate_limit(name="automatic-programming", limit=20, window=3600)
def admin_automatic_program(request):
    from account.services.exercise_goal_service import ExerciseGoalService

    actor = request.user
    coach_mode = bool(getattr(actor, "is_coach", False) and not getattr(actor, "is_admin", False))
    if not getattr(actor, "is_admin", False) and not (
        coach_mode and getattr(actor, "can_use_automatic_programming", False)
    ):
        return error("permission_denied", "Administrator or enabled coach access is required.", status=403)
    raw = request_data(request)
    allowed = {
        "action", "user", "program_type", "training_methods", "difficulty", "gender", "abnormalities",
        "sessions_per_week", "movements_per_session", "goal", "target_secondary_movement_types",
        "secondary_movement_counts_json", "session_movement_targets_json", "seed", "is_published", "requires_payment", "price",
        "athlete_goal", "template",
    }
    unknown = set(raw) - allowed
    if unknown:
        raise ValidationError({"fields": f"Unsupported fields: {', '.join(sorted(unknown))}"})
    action = raw.get("action", "preview")
    if action not in {"preview", "save"}:
        raise ValidationError({"action": "action must be preview or save."})
    form_data = QueryDict("", mutable=True)
    multivalue_fields = {"training_methods", "abnormalities", "target_secondary_movement_types"}
    json_fields = {"secondary_movement_counts_json", "session_movement_targets_json"}
    for key, value in raw.items():
        if key == "action":
            continue
        if key in multivalue_fields:
            if not isinstance(value, list):
                raise ValidationError({key: "This field must be an array."})
            form_data.setlist(key, [str(item) for item in value])
        elif key in json_fields:
            form_data[key] = json.dumps(value, ensure_ascii=False)
        elif isinstance(value, bool):
            form_data[key] = "true" if value else ""
        else:
            form_data[key] = str(value)
    requested_user_id = form_data.get("user")
    if form_data.get("goal"):
        form_data["goal"] = ExerciseGoalService.canonical_key(form_data["goal"])
    if form_data.get("athlete_goal"):
        form_data["athlete_goal"] = ExerciseGoalService.canonical_key(form_data["athlete_goal"])
    athlete_queryset = User.objects.filter(is_admin=False, is_coach=False)
    if coach_mode:
        athlete_queryset = athlete_queryset.filter(coach=actor, is_active=True)
    target_user = get_object_or_404(athlete_queryset, pk=parse_int(requested_user_id, field="user"))

    explicit_athlete_goal = bool(form_data.get("athlete_goal"))
    template = None
    if form_data.get("template"):
        template = ProgramTemplateService.accessible_to(actor).filter(pk=form_data.get("template")).first()
        if template is None:
            raise ValidationError({"template": "The selected template is not available to this account."})
        template_values = ProgramTemplateService.generation_payload(template)
        form_data["sessions_per_week"] = str(template.days_per_week)
        form_data["difficulty"] = str(template.difficulty_id)
        if not form_data.get("session_movement_targets_json") and template_values["session_movement_targets"]:
            form_data["session_movement_targets_json"] = json.dumps(
                template_values["session_movement_targets"], ensure_ascii=False
            )
        if not form_data.get("secondary_movement_counts_json") and template_values["secondary_movement_counts"]:
            form_data["secondary_movement_counts_json"] = json.dumps(
                template_values["secondary_movement_counts"], ensure_ascii=False
            )
        template_target_ids = template_values["target_secondary_movement_type_ids"] or [
            entry.get("movement_type")
            for target in template_values["session_movement_targets"]
            if isinstance(target, dict)
            for entry in target.get("targets", [])
            if isinstance(entry, dict) and entry.get("movement_type")
        ]
        if not form_data.getlist("target_secondary_movement_types"):
            form_data.setlist(
                "target_secondary_movement_types",
                [str(value) for value in template_target_ids],
            )
        form_data["athlete_goal"] = ExerciseGoalService.canonical_key(
            form_data.get("athlete_goal") or template.training_goal
        )
    form_data["title"] = f"برنامه خودکار - {target_user.display_name or target_user.fullname}"[:150]
    form = AutomaticProgrammingForm(data=form_data, actor=actor)
    form.fields["user"].queryset = athlete_queryset
    if not form.is_valid():
        raise ValidationError(form.errors)
    values = form.cleaned_data
    profile = AthleteTrainingProfile.objects.filter(user=values["user"]).first()
    training_goal = values.get("athlete_goal") or (profile.primary_goal if profile else None)
    goal_source = "admin" if explicit_athlete_goal else (
        "template" if template else "athlete_profile" if training_goal else "program_goal"
    )
    preview = automatic_program_service.generate(
        difficulty=values["difficulty"],
        gender=values.get("gender"),
        abnormalities=values.get("abnormalities", []),
        sessions=values["sessions_per_week"],
        movements=values["movements_per_session"],
        goal=values["goal"],
        training_goal=training_goal,
        training_goal_source=goal_source,
        training_methods=values.get("training_methods"),
        secondary_movement_counts=values.get("secondary_movement_counts_json"),
        session_movement_targets=values.get("session_movement_targets_json"),
        athlete=values["user"],
        seed=values.get("seed"),
        template=template,
    )
    if action == "preview":
        return ok({"preview": preview})

    target = values["user"]
    program_form = WorkoutProgramForm(data={
        "title": f"برنامه خودکار - {target.display_name or target.fullname}"[:150],
        "program_type": values.get("program_type") or "normal",
        "start_date": timezone.localdate(),
        "is_published": True if coach_mode else values.get("is_published", True),
        "requires_payment": False if coach_mode else values.get("requires_payment", False),
        "price": 0 if coach_mode else values.get("price") or 0,
    })
    if not program_form.is_valid():
        raise ValidationError(program_form.errors)
    metadata = preview.get("metadata") or {}
    with transaction.atomic():
        locked_recommendations = TrainingRecommendationReviewService.lock_applicable_recommendations(
            athlete=target,
            recommendation_ids=metadata.get("progression_recommendation_ids") or [],
            program=program_form.instance,
        )
        program = gym_program_service.save_program(
            program_form=program_form,
            target_user=target,
            prescribed_by=actor,
            days_payload=preview["days"],
            correctives_payload=preview["correctives"],
            generation_metadata=metadata,
        )
        TrainingRecommendationReviewService.mark_applied(
            recommendations=locked_recommendations,
            program=program,
        )
        ExercisePreferenceService.record_program_edits(
            athlete=target,
            actor=actor,
            days_payload=preview["days"],
        )
    _audit(request.user, "workout_program.auto_create", object_type="workout_program", object_id=program.pk)
    return ok({
        "program": {
            "id": program.pk,
            "title": program.title,
            "is_published": program.is_published,
            "revision": program.revision,
            "algorithm_version": program.algorithm_version,
        },
        "warnings": preview.get("warnings", []),
        "metadata": preview.get("metadata", {}),
    }, status=201)


@api_endpoint
@api_methods("GET")
@api_auth_required
@rate_limit(name="program-templates", limit=60, window=3600)
def admin_program_templates(request):
    actor = request.user
    coach_mode = bool(getattr(actor, "is_coach", False) and not getattr(actor, "is_admin", False))
    if not getattr(actor, "is_admin", False) and not (
        coach_mode and getattr(actor, "can_use_automatic_programming", False)
    ):
        return error("permission_denied", "Administrator or enabled coach access is required.", status=403)
    templates = ProgramTemplateService.accessible_to(actor)
    query = (request.GET.get("q") or "").strip()
    if len(query) > 120:
        raise ValidationError({"q": "Search query must be at most 120 characters."})
    if query:
        templates = templates.filter(Q(title__icontains=query))

    def serialize(template):
        return {
            "id": template.pk,
            "title": template.title,
            "owner_id": template.owner_id,
            "training_goal": template.training_goal,
            "difficulty": {
                "id": template.difficulty_id,
                "name": template.difficulty.name,
            },
            "days_per_week": template.days_per_week,
            "weeks": template.weeks,
            "payload": template.payload,
            "updated_at": timezone.localtime(template.updated_at).isoformat(),
        }

    return ok(_page(request, templates, serialize))


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
@api_methods("GET")
@api_auth_required
def analysis_me(request):
    return ok(_analysis_payload(request.user, start=request.GET.get("start"), end=request.GET.get("end")))


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
@api_methods("GET")
@api_auth_required
@api_admin_required
def admin_user_analysis(request, user_id):
    user = get_object_or_404(User, pk=user_id)
    _audit(request.user, "user.analysis.read", object_type="user", object_id=user.pk)
    return ok({
        "user": _serialize_user(user),
        **_analysis_payload(user, start=request.GET.get("start"), end=request.GET.get("end")),
    })


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


def _coach_athlete(request, athlete_id):
    return get_object_or_404(User, pk=athlete_id, coach=request.user, is_active=True, is_admin=False, is_coach=False)


@api_endpoint
@api_methods("GET")
@api_auth_required
@api_coach_required
def coach_athlete_analysis(request, athlete_id):
    athlete = _coach_athlete(request, athlete_id)
    _audit(request.user, "athlete.analysis.read", object_type="user", object_id=athlete.pk)
    return ok({
        "user": _serialize_user(athlete),
        **_analysis_payload(athlete, start=request.GET.get("start"), end=request.GET.get("end"), coach_scope=True),
    })


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
@api_coach_required
@rate_limit(name="coach-program-create", limit=30, window=3600)
def coach_athlete_programs(request, athlete_id):
    athlete = _coach_athlete(request, athlete_id)
    if request.method == "GET":
        programs = gym_program_service.get_program_queryset().filter(user=athlete, prescribed_by=request.user)
        from api.v1.common import _serialize_program
        return ok({"items": [_serialize_program(request, program) for program in programs]})
    program = _create_program(request, athlete, coach_mode=True)
    return ok({"program": {"id": program.pk, "title": program.title, "is_published": program.is_published}}, status=201)


@api_endpoint
@api_methods("GET")
@api_auth_required
@api_coach_required
def coach_athlete_training_recommendations(request, athlete_id):
    from account.services.progression_recommendation_service import ProgressionRecommendationService

    athlete = _coach_athlete(request, athlete_id)
    items = TrainingAdjustmentRecommendation.objects.filter(user=athlete).select_related(
        "program", "source_session", "program_exercise", "exercise", "reviewed_by"
    )[:100]
    return ok({"items": [ProgressionRecommendationService.serialize(item) for item in items]})


@transaction.atomic
@api_endpoint
@api_methods("PATCH")
@api_auth_required
@api_coach_required
def coach_review_training_recommendation(request, athlete_id, recommendation_id):
    from account.services.progression_recommendation_service import ProgressionRecommendationService
    from account.services.training_data_service import TrainingDataService
    from account.services.training_recommendation_review_service import TrainingRecommendationReviewService

    athlete = _coach_athlete(request, athlete_id)
    recommendation = get_object_or_404(TrainingAdjustmentRecommendation, pk=recommendation_id, user=athlete)
    data = request_data(request)
    if not isinstance(data, dict):
        raise ValidationError({"body": "Expected a JSON object."})
    allowed = {"status", "reviewed_load_kg", "coach_note"}
    unknown = set(data) - allowed
    if unknown:
        raise ValidationError({"fields": f"Unsupported fields: {', '.join(sorted(unknown))}"})
    status = data.get("status")
    if status not in {
        TrainingAdjustmentRecommendation.Status.ACCEPTED,
        TrainingAdjustmentRecommendation.Status.EDITED,
        TrainingAdjustmentRecommendation.Status.REJECTED,
    }:
        raise ValidationError({"status": "status must be accepted, edited, or rejected."})
    reviewed_load = TrainingDataService._decimal(data.get("reviewed_load_kg"), "reviewed_load_kg")
    is_deload = recommendation.recommendation_type == TrainingAdjustmentRecommendation.RecommendationType.DELOAD
    if status == TrainingAdjustmentRecommendation.Status.EDITED and reviewed_load is None and not is_deload:
        raise ValidationError({"reviewed_load_kg": "A reviewed load is required when status is edited."})
    note = str(data.get("coach_note") or "").strip()
    if len(note) > 2000:
        raise ValidationError({"coach_note": "coach_note must be 2,000 characters or fewer."})
    recommendation, _merged_count = TrainingRecommendationReviewService.review(
        staff_user=request.user,
        recommendation_id=recommendation.pk,
        status=status,
        reviewed_load=reviewed_load,
        coach_note=note,
    )
    _audit(request.user, "training.recommendation.review", object_type="training_recommendation", object_id=recommendation.pk)
    return ok({"recommendation": ProgressionRecommendationService.serialize(recommendation), "applied_to_program": False})


def _measurement_payload(data) -> dict:
    """Accept numeric measurements and normalize an optional Jalali date."""
    if not isinstance(data, dict):
        raise ValidationError({"body": "Expected a JSON object."})
    import jdatetime

    cleaned = {key: value for key, value in data.items() if key not in {"date", "measured_at"}}
    jalali = str(data.get("date") or data.get("measured_at_jalali") or "").strip()
    if jalali:
        gregorian = _parse_jalali_range(jalali, None)[0]
        if gregorian is None:
            raise ValidationError({"date": "تاریخ باید به شکل 1404/10/12 باشد."})
        cleaned["measured_at_jalali"] = jdatetime.date.fromgregorian(date=gregorian).strftime("%Y/%m/%d")
    return cleaned


def _backdate_record(record, gregorian_date):
    """Compatibility helper for legacy measurement rows."""
    if gregorian_date is None:
        return
    from datetime import datetime, time

    moment = timezone.make_aware(
        datetime.combine(gregorian_date, time(12, 0)), timezone.get_current_timezone()
    )
    type(record).objects.filter(pk=record.pk).update(measured_at=moment)
    record.measured_at = moment


@api_endpoint
@api_methods("POST")
@api_auth_required
@api_coach_required
@rate_limit(name="coach-analysis-write", limit=60, window=3600)
def coach_athlete_measurements(request, athlete_id, kind):
    """Let a coach record circumference, caliper or health data (requirement 3c)."""
    athlete = _coach_athlete(request, athlete_id)
    payload = request_data(request)
    if kind == "health":
        if not isinstance(payload, dict):
            raise ValidationError({"body": "Expected a JSON object."})
        data = payload
    else:
        data = _measurement_payload(payload)

    if kind == "circumference":
        instance = admin_portal_service.get_or_create_circumference(athlete)
        form = AdminCircumferenceForm(data, instance=instance)
        if not form.is_valid():
            raise ValidationError(form.errors)
        from account.models import MeasurementSource

        record = admin_portal_service.save_circumference(
            user=athlete, form=form, actor=request.user, source=MeasurementSource.COACH
        )
        action = "measurement.circumference.create"
    elif kind == "caliper":
        instance = admin_portal_service.get_or_create_caliper(athlete)
        form = AdminCaliperForm(data, instance=instance)
        if not form.is_valid():
            raise ValidationError(form.errors)
        from account.models import MeasurementSource

        record = admin_portal_service.save_caliper(
            user=athlete, form=form, actor=request.user, source=MeasurementSource.COACH
        )
        action = "measurement.caliper.create"
    elif kind == "health":
        if "recorded_at" not in data:
            # The health form stores a Gregorian date; translate the Jalali
            # ``date`` the mobile editor sends, defaulting to "today".
            jalali = str(data.get("date") or "").strip()
            gregorian = _parse_jalali_range(jalali, None)[0] if jalali else timezone.localdate()
            if gregorian is None:
                raise ValidationError({"date": "تاریخ باید به شکل 1404/10/12 باشد."})
            data = {**{k: v for k, v in data.items() if k != "date"}, "recorded_at": gregorian.isoformat()}
        else:
            data = {key: value for key, value in data.items() if key != "date"}
        category = data.get("category")
        safe_categories = {UserHealthRecord.CategoryChoices.INJURY, UserHealthRecord.CategoryChoices.ABNORMALITY}
        if category not in safe_categories:
            raise ValidationError({"category": "Coaches may record training-relevant injury or abnormality entries only."})
        allowed_health_fields = {
            "category", "body_region", "affected_side", "severity", "onset_date", "status",
            "resolved_date", "training_restriction", "training_restriction_notes", "recorded_at",
        }
        unsupported = set(data) - allowed_health_fields
        if unsupported:
            raise ValidationError({"fields": "Unsupported health fields: " + ", ".join(sorted(unsupported))})
        data["title"] = dict(UserHealthRecord.CategoryChoices.choices).get(category, "سابقه تمرینی")
        form = AdminHealthRecordForm(data)
        if not form.is_valid():
            raise ValidationError(form.errors)
        record = admin_portal_service.save_health_record(user=athlete, form=form)
        action = "health_record.create"
    else:
        return error("not_found", "Unknown measurement kind.", status=404)

    _audit(request.user, action, object_type="user", object_id=athlete.pk)
    measurement = {"id": record.pk, "kind": kind}
    if kind in {"circumference", "caliper"}:
        measurement.update(
            {
                "measured_at": json_value(record.measured_at),
                "source": record.measurement_source,
                "method": record.measurement_method,
                "protocol_version": record.protocol_version,
                "trial_number": record.trial_number,
            }
        )
    return ok({"measurement": measurement}, status=201)


@api_endpoint
@api_methods("GET", "POST")
@api_auth_required
@api_admin_required
@rate_limit(name="admin-client-upload", limit=50, window=3600)
def admin_user_documents(request, user_id):
    user = get_object_or_404(User, pk=user_id, is_admin=False)
    return _document_collection(request, user, allow_post=True, management_path=f"/api/v1/admin/users/{user.pk}/documents")


@api_endpoint
@api_methods("GET", "POST")
@api_auth_required
@api_admin_required
@rate_limit(name="admin-program-create", limit=100, window=3600)
def admin_user_programs(request, user_id):
    user = get_object_or_404(User, pk=user_id, is_admin=False)
    if request.method == "GET":
        programs = gym_program_service.get_program_queryset().filter(user=user)
        from api.v1.common import _serialize_program
        return ok({"items": [_serialize_program(request, program) for program in programs]})
    program = _create_program(request, user)
    return ok({"program": {"id": program.pk, "title": program.title, "is_published": program.is_published}}, status=201)


@api_endpoint
@api_methods("GET")
@api_auth_required
@api_admin_required
def admin_transactions(request):
    _audit(request.user, "transactions.list.read", object_type="transaction_list")
    queryset = Order.objects.select_related("user").prefetch_related("items__product").order_by("-created_at")
    status = (request.GET.get("status") or "").strip()
    if status in {choice for choice, _label in Order.PaymentStatus.choices}:
        queryset = queryset.filter(status=status)
    if request.GET.get("user_id"):
        queryset = queryset.filter(user_id=parse_int(request.GET["user_id"], field="user_id"))
    orders = _page(request, queryset, lambda order: {
        "id": order.pk,
        "order_number": order.order_number,
        "user": {"id": order.user_id, "fullname": order.user.fullname, "phone": order.user.phone},
        "subtotal_price": order.subtotal_price,
        "discount_amount": order.discount_amount,
        "total_price": order.total_price,
        "status": order.status,
        "is_paid": order.is_paid,
        "created_at": json_value(order.created_at),
        "paid_at": json_value(order.paid_at),
        "ref_id": order.ref_id,
        "items": [{"course_id": item.product_id, "title": item.product.title, "price": item.price} for item in order.items.all()],
    })
    document_payments = ClientDocumentPayment.objects.select_related("user", "document").order_by("-created_at")
    program_payments = WorkoutProgramPayment.objects.select_related("user", "program").order_by("-created_at")
    if status in {choice for choice, _label in ClientDocumentPayment.Status.choices}:
        document_payments = document_payments.filter(status=status)
    if status in {choice for choice, _label in WorkoutProgramPayment.Status.choices}:
        program_payments = program_payments.filter(status=status)
    if request.GET.get("user_id"):
        user_id = parse_int(request.GET["user_id"], field="user_id")
        document_payments = document_payments.filter(user_id=user_id)
        program_payments = program_payments.filter(user_id=user_id)
    return ok({
        "orders": orders,
        "document_payments": _page(request, document_payments, lambda payment: {
            "id": payment.pk,
            "user": {"id": payment.user_id, "fullname": payment.user.fullname, "phone": payment.user.phone},
            "document": {"id": payment.document_id, "title": payment.document.title},
            "amount": payment.amount,
            "status": payment.status,
            "created_at": json_value(payment.created_at),
            "paid_at": json_value(payment.paid_at),
            "ref_id": payment.ref_id,
        }),
        "workout_program_payments": _page(request, program_payments, lambda payment: {
            "id": payment.pk,
            "user": {"id": payment.user_id, "fullname": payment.user.fullname, "phone": payment.user.phone},
            "program": {"id": payment.program_id, "title": payment.program.title},
            "amount": payment.amount,
            "status": payment.status,
            "created_at": json_value(payment.created_at),
            "paid_at": json_value(payment.paid_at),
            "ref_id": payment.ref_id,
        }),
    })


@api_endpoint
@api_methods("GET", "POST")
@api_auth_required
@api_admin_required
def admin_courses(request):
    if request.method == "GET":
        from api.v1.common import _serialize_series
        queryset = SeriesModel.objects.select_related("author", "language_kinds").order_by("-pk")
        return ok(_page(request, queryset, lambda series: {**_serialize_series(request, series), "is_published": series.is_published}))

    allowed_fields = {"title", "free", "category_id", "is_completed", "description", "introduction", "main_price", "discount_price"}
    if set(request.POST) - allowed_fields or set(request.FILES) - {"image"}:
        raise ValidationError({"fields": "Unsupported course upload fields."})
    title = (request.POST.get("title") or "").strip()
    if not title or len(title) > 50:
        raise ValidationError({"title": "title is required and must be at most 50 characters."})
    image = request.FILES.get("image")
    if not image:
        raise ValidationError({"image": "Course image is required."})
    validate_image_upload(image)
    free = _multipart_bool(request.POST.get("free"), field="free", default=True)
    category_id = request.POST.get("category_id")
    category = get_object_or_404(Category, pk=parse_int(category_id, field="category_id")) if category_id else None
    series = SeriesModel(
        title=title,
        image=image,
        author=request.user,
        language_kinds=category,
        free=free,
        is_published=True,
        is_compeleted=_multipart_bool(request.POST.get("is_completed"), field="is_completed"),
        description=(request.POST.get("description") or "").strip(),
        introdution_course=(request.POST.get("introduction") or "").strip(),
        main_price=(request.POST.get("main_price") or "").strip()[:20],
        discount_price=(request.POST.get("discount_price") or "").strip()[:20],
    )
    series.full_clean()
    series.save()
    cache.delete(f"{HOME_PAGE_CACHE_PREFIX}:home:v1")
    cache.delete(SERIES_COUNT_CACHE_KEY)
    _audit(request.user, "course.create", object_type="course", object_id=series.pk)
    from api.v1.common import _serialize_series
    return ok({**_serialize_series(request, series), "is_published": series.is_published}, status=201)


@api_endpoint
@api_methods("GET", "PATCH")
@api_auth_required
@api_admin_required
def admin_course_detail(request, course_id):
    if request.method == "GET":
        from api.v1.common import _serialize_series

        course = get_object_or_404(
            SeriesModel.objects.select_related("author", "language_kinds")
            .prefetch_related("seasons__episodes"),
            pk=course_id,
        )
        payload = {**_serialize_series(request, course), "is_published": course.is_published}
        payload["seasons"] = [
            {
                "id": season.pk,
                "number": season.number,
                "title": season.title,
                "episodes": [
                    {
                        "id": episode.pk,
                        "episode_number": episode.episode_number,
                        "title": episode.title,
                        "duration": episode.duration,
                    }
                    for episode in season.episodes.all()
                ],
            }
            for season in course.seasons.all()
        ]
        return ok(payload)
    data = request_data(request)
    if set(data) != {"is_published"} or not isinstance(data.get("is_published"), bool):
        raise ValidationError({"is_published": "A boolean is_published field is required."})
    course = get_object_or_404(SeriesModel, pk=course_id)
    course.is_published = data["is_published"]
    course.save(update_fields=["is_published"])
    cache.delete(f"{HOME_PAGE_CACHE_PREFIX}:home:v1")
    cache.delete(SERIES_COUNT_CACHE_KEY)
    _audit(request.user, "course.publication.update", object_type="course", object_id=course.pk)
    return ok({"id": course.pk, "is_published": course.is_published})


@api_endpoint
@api_methods("POST")
@api_auth_required
@api_admin_required
def admin_course_seasons(request, course_id):
    data = request_data(request)
    if set(data) - {"number", "title"} or not str(data.get("title") or "").strip():
        raise ValidationError({"season": "title is required; only number and title are accepted."})
    course = get_object_or_404(SeriesModel, pk=course_id)
    number = parse_int(data.get("number", course.seasons.count() + 1), field="number")
    if number < 1:
        raise ValidationError({"number": "number must be greater than zero."})
    season = Season.objects.create(series=course, number=number, title=str(data["title"]).strip()[:50])
    _audit(request.user, "course.season.create", object_type="season", object_id=season.pk)
    return ok({"id": season.pk, "series_id": course.pk, "number": season.number, "title": season.title}, status=201)


@api_endpoint
@api_methods("POST")
@api_auth_required
@api_admin_required
@rate_limit(name="admin-course-video-upload", limit=20, window=3600)
def admin_season_episodes(request, season_id):
    season = get_object_or_404(Season.objects.select_related("series"), pk=season_id)
    if set(request.POST) - {"title", "episode_number"} or set(request.FILES) - {"video_file"}:
        raise ValidationError({"fields": "Unsupported episode upload fields."})
    title = (request.POST.get("title") or "").strip()
    video = request.FILES.get("video_file")
    if not title or len(title) > 255:
        raise ValidationError({"title": "title is required and must be at most 255 characters."})
    if video:
        validate_video_upload(video)
    number = parse_int(request.POST.get("episode_number", season.episodes.count() + 1), field="episode_number")
    if number < 1:
        raise ValidationError({"episode_number": "episode_number must be greater than zero."})
    episode = Episode.objects.create(season=season, title=title, episode_number=number, video_file=video)
    _audit(request.user, "course.episode.create", object_type="episode", object_id=episode.pk)
    return ok({"id": episode.pk, "season_id": season.pk, "title": episode.title, "episode_number": episode.episode_number, "duration": episode.duration}, status=201)


def _serialize_blog_article(request, article, *, detail=False):
    payload = {
        "id": article.pk,
        "slug": article.slug,
        "title": article.title,
        "reading_time": article.reading_time,
        "excerpt": article.article_excerpt,
        "publication_status": article.publication_status,
        "publication_status_label": article.get_publication_status_display(),
        "image": absolute_file_url(request, article.image) if article.image else None,
        "author": {"id": article.author_id, "fullname": article.author.fullname} if article.author_id else None,
    }
    if detail:
        payload["description"] = article.article_description
    return payload


def _validate_blog_submission(request):
    """Extract and validate the fields a coach may submit for a new post."""
    allowed = {"title", "reading_time", "article_excerpt", "article_description", "built_in", "subcategory"}
    if set(request.POST) - allowed or set(request.FILES) - {"image"}:
        raise ValidationError({"fields": "Unsupported article fields."})
    title = (request.POST.get("title") or "").strip()
    if not title or len(title) > 100:
        raise ValidationError({"title": "title is required and must be at most 100 characters."})
    image = request.FILES.get("image")
    if not image:
        raise ValidationError({"image": "An article image is required."})
    validate_image_upload(image)
    try:
        reading_time = parse_int(request.POST.get("reading_time", 5), field="reading_time")
    except ValueError:
        reading_time = 5
    if reading_time < 1 or reading_time > 240:
        raise ValidationError({"reading_time": "reading_time must be between 1 and 240 minutes."})
    return {
        "title": title,
        "reading_time": reading_time,
        "article_excerpt": (request.POST.get("article_excerpt") or "").strip(),
        "article_description": (request.POST.get("article_description") or "").strip(),
        "built_in": (request.POST.get("built_in") or "").strip(),
        "subcategory": request.POST.get("subcategory") or None,
    }


@api_endpoint
@api_methods("GET", "POST")
@api_auth_required
@api_coach_required
@rate_limit(name="coach-blog-submit", limit=10, window=3600)
def coach_articles(request):
    """A coach's own posts. New submissions stay pending until an admin approves."""
    if request.method == "GET":
        queryset = (
            ArticleBlogModel.objects.filter(author=request.user)
            .select_related("author")
            .order_by("-id")[:50]
        )
        return ok({"items": [_serialize_blog_article(request, article) for article in queryset]})

    from home.models import BlogSubCategory

    fields = _validate_blog_submission(request)
    subcategory = None
    if fields["subcategory"]:
        # The coach may only attach the post to an existing subcategory, and may
        # not smuggle another author or status through the payload.
        subcategory = get_object_or_404(BlogSubCategory, pk=parse_int(fields["subcategory"], field="subcategory"))
    article = ArticleBlogModel(
        title=fields["title"],
        image=request.FILES["image"],
        author=request.user,
        reading_time=fields["reading_time"],
        article_excerpt=fields["article_excerpt"],
        article_description=fields["article_description"],
        built_in=fields["built_in"],
        subcategory=subcategory,
        publication_status=ArticleBlogModel.PublicationStatus.PENDING,
    )
    article.full_clean()
    article.save()
    invalidate_blog_caches()
    _audit(request.user, "coach.article.submit", object_type="article", object_id=article.pk)
    return ok({"article": _serialize_blog_article(request, article)}, status=201)


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


@api_endpoint
@api_methods("GET")
@api_auth_required
@api_admin_required
def admin_articles(request):
    """Moderation queue of all articles, filterable by publication status."""
    queryset = ArticleBlogModel.objects.select_related("author").order_by("-id")
    status = (request.GET.get("status") or "").strip()
    if status in {choice for choice, _label in ArticleBlogModel.PublicationStatus.choices}:
        queryset = queryset.filter(publication_status=status)
    return ok(_page(request, queryset, lambda article: _serialize_blog_article(request, article, detail=True)))


@api_endpoint
@api_methods("PATCH")
@api_auth_required
@api_admin_required
def admin_article_moderate(request, article_id):
    """Approve or reject a coach-authored post so it can appear on the site."""
    data = request_data(request)
    valid_statuses = {choice for choice, _label in ArticleBlogModel.PublicationStatus.choices}
    if set(data) != {"publication_status"} or data.get("publication_status") not in valid_statuses:
        raise ValidationError({"publication_status": "A valid publication_status is required."})
    with transaction.atomic():
        article = get_object_or_404(ArticleBlogModel.objects.select_for_update(), pk=article_id)
        article.publication_status = data["publication_status"]
        article.save(update_fields=["publication_status"])
    invalidate_blog_caches()
    _audit(request.user, "admin.article.moderate", object_type="article", object_id=article.pk)
    return ok({"article": _serialize_blog_article(request, article, detail=True)})


# ---------------------------------------------------------------------------
# Role management, coach applications and exercise suggestions
# ---------------------------------------------------------------------------


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
