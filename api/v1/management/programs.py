"""Workout program, template, and progression endpoints for staff."""

from __future__ import annotations

import json

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import QueryDict
from django.shortcuts import get_object_or_404
from django.utils import timezone

from account.admin_forms import AutomaticProgrammingForm, WorkoutProgramForm
from account.models import AthleteTrainingProfile, User, TrainingAdjustmentRecommendation
from account.services.gym_program_service import GymProgramService
from account.services.automatic_program_service import AutomaticProgramService
from account.services.exercise_preference_service import ExercisePreferenceService
from account.services.program_template_service import ProgramTemplateService
from account.services.training_recommendation_review_service import (
    TrainingRecommendationReviewService,
)
from api.decorators import api_auth_required, api_endpoint, api_methods
from api.limits import rate_limit
from api.responses import error, ok
from api.utils import parse_int, request_data
from api.v1.permissions import api_admin_required, api_coach_required

from .support import _audit, _page, _coach_athlete


gym_program_service = GymProgramService()


automatic_program_service = AutomaticProgramService()


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
