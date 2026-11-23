from api.v1.common import *
from account.services.client_file_service import open_client_file

@api_endpoint
@api_methods("GET", "PATCH", "POST")
@api_auth_required
def me(request):
    if request.method == "GET":
        return ok({"user": _serialize_user(request, request.user), "metrics": profile_service.get_profile_metrics(request.user)})
    # POST is used by mobile clients to send the avatar as multipart form-data;
    # PATCH keeps the plain-JSON path for the other profile fields.
    data = request_data(request)
    allowed = {"first_name", "last_name", "display_name", "email", "biography", "web_site", "github", "linkdin", "telegram"}
    unknown = set(data) - allowed
    if unknown:
        raise ValidationError({"fields": [f"Unsupported fields: {', '.join(sorted(unknown))}"]})
    update_fields = []
    for field in allowed:
        if field in data:
            value = data[field]
            if field == "email":
                from django.forms import EmailField

                value = EmailField(required=False).clean(value)
                if request.user.__class__.objects.filter(email=value).exclude(pk=request.user.pk).exists():
                    raise ValidationError({"email": "This email is already in use."})
            setattr(request.user, field, value)
            update_fields.append(field)
    # The avatar arrives as multipart form-data, so it is read from FILES
    # rather than the JSON/POST payload. Only valid images are accepted.
    avatar = (request.FILES or {}).get("profile_picture")
    if avatar is not None:
        request.user.profile_picture = validate_image_upload(avatar)
        update_fields.append("profile_picture")
    if update_fields:
        request.user.save(update_fields=update_fields)
    return ok({"user": _serialize_user(request, request.user)})

@api_endpoint
@api_methods("PATCH")
@api_auth_required
def metric(request, metric_name):
    data = request_data(request)
    form_map = {
        "weight": (WeightForm, "weight", "weight_kg"),
        "height": (HeightForm, "height", "height_cm"),
        "birth_date": (BirthDateForm, "birth_date", "birth_date_jalali"),
        "blood_group": (BloodGroupForm, "blood_group", "blood_group"),
    }
    if metric_name not in form_map:
        return error("not_found", "Metric not found.", status=404)
    form_class, input_name, model_field = form_map[metric_name]
    allowed_fields = {input_name}
    if metric_name in {"weight", "height"}:
        allowed_fields.update({"measured_at", "measurement_method", "protocol_version"})
    _reject_unknown(data, allowed_fields)
    form = form_class(data={key: value for key, value in data.items() if key in allowed_fields})
    if not form.is_valid():
        raise ValidationError(form.errors)
    metadata = {
        name: form.cleaned_data.get(name)
        for name in ("measured_at", "measurement_method", "protocol_version")
        if name in form.cleaned_data
    }
    profile_service.update_profile_metric(
        user=request.user,
        field_name=model_field,
        value=form.cleaned_data[input_name],
        **metadata,
    )
    return ok({"user": _serialize_user(request, request.user), "metric": metric_name})


@api_endpoint
@api_methods("GET", "POST")
@api_auth_required
def measurements(request, kind):
    from account.services.measurement_service import MeasurementService
    from account.models import MeasurementSource

    if kind not in MeasurementService.MODEL_BY_KIND:
        return error("not_found", "Measurement type not found.", status=404)
    if request.method == "GET":
        records = MeasurementService.history(user=request.user, kind=kind)
        return ok({"items": [MeasurementService.serialize(record) for record in records]})
    record = MeasurementService.create(
        user=request.user,
        payload=request_data(request),
        kind=kind,
        source=MeasurementSource.SELF_REPORTED,
        actor=request.user,
    )
    return ok({"measurement": MeasurementService.serialize(record)}, status=201)


@api_endpoint
@api_methods("GET", "PATCH")
@api_auth_required
def training_preferences(request):
    from account.froms import AthleteTrainingProfileForm
    from account.models import AthleteTrainingProfile
    from account.services.exercise_goal_service import ExerciseGoalService

    profile = AthleteTrainingProfile.objects.filter(user=request.user).first()
    goal_catalog = ExerciseGoalService.catalog()
    default_goal = next((item["key"] for item in goal_catalog if item["key"] == "general"), "general")
    if request.method == "GET":
        equipment = list(profile.available_equipment.order_by("name").values("id", "name")) if profile else []
        primary_goal = profile.primary_goal if profile else default_goal
        return ok({
            "preferences": {
                "primary_goal": primary_goal,
                "primary_goal_label": ExerciseGoalService.label(primary_goal),
                "goal_catalog": goal_catalog,
                "target_date": profile.target_date.isoformat() if profile and profile.target_date else None,
                "sessions_per_week": profile.sessions_per_week if profile else None,
                "experience_years": profile.experience_years if profile else None,
                "session_duration_minutes": profile.session_duration_minutes if profile else None,
                "preferred_days": profile.preferred_days if profile else [],
                "available_equipment": equipment,
            }
        })
    data = request_data(request)
    if "primary_goal" in data:
        if not isinstance(data["primary_goal"], str):
            raise ValidationError({"primary_goal": "primary_goal must be a goal key."})
        data["primary_goal"] = ExerciseGoalService.canonical_key(data["primary_goal"])
    allowed = set(AthleteTrainingProfileForm.Meta.fields)
    _reject_unknown(data, allowed)
    if "preferred_days" in data and not isinstance(data["preferred_days"], list):
        raise ValidationError({"preferred_days": "preferred_days must be an array."})
    if "available_equipment" in data and not isinstance(data["available_equipment"], list):
        raise ValidationError({"available_equipment": "available_equipment must be an array of IDs."})
    if profile is not None:
        current = {
            "primary_goal": profile.primary_goal,
            "target_date": profile.target_date.isoformat() if profile.target_date else "",
            "sessions_per_week": profile.sessions_per_week,
            "experience_years": profile.experience_years,
            "session_duration_minutes": profile.session_duration_minutes,
            "preferred_days": profile.preferred_days,
            "available_equipment": list(profile.available_equipment.values_list("pk", flat=True)),
        }
        current.update(data)
        data = current
    form = AthleteTrainingProfileForm(data=data, instance=profile)
    if not form.is_valid():
        raise ValidationError(form.errors)
    profile = form.save(commit=False)
    profile.user = request.user
    profile.save()
    form.save_m2m()
    return ok({
        "saved": True,
        "preferences": {
            "primary_goal": profile.primary_goal,
            "primary_goal_label": ExerciseGoalService.label(profile.primary_goal),
            "goal_catalog": goal_catalog,
            "target_date": profile.target_date.isoformat() if profile.target_date else None,
            "sessions_per_week": profile.sessions_per_week,
            "experience_years": profile.experience_years,
            "session_duration_minutes": profile.session_duration_minutes,
            "preferred_days": profile.preferred_days or [],
            "available_equipment": list(profile.available_equipment.order_by("name").values("id", "name")),
        },
    }, status=200)


@api_endpoint
@api_methods("GET")
@api_auth_required
def training_insights(request):
    """Expose the same recovery, adherence and e1RM inputs used by generation."""
    from datetime import timedelta

    from account.models import AthleteTrainingProfile, ExerciseStrengthEstimate, Muscle
    from account.services.automatic_program_service import AutomaticProgramService
    from account.services.training_adherence_service import TrainingAdherenceService

    profile = AthleteTrainingProfile.objects.filter(user=request.user).first()
    fatigue = AutomaticProgramService._muscle_fatigue_snapshot(
        request.user.pk,
        profile.experience_years if profile else None,
    )
    muscles = Muscle.objects.in_bulk(fatigue.keys())
    estimates = ExerciseStrengthEstimate.objects.filter(
        user=request.user,
        measured_on__gte=timezone.now() - timedelta(days=90),
    ).select_related("exercise").order_by("-measured_on", "-pk")[:100]
    return ok({
        "adherence": TrainingAdherenceService.snapshot(request.user),
        "muscle_recovery": [
            {
                "muscle_id": muscle_id,
                "name": muscles[muscle_id].name if muscle_id in muscles else "",
                **values,
            }
            for muscle_id, values in sorted(fatigue.items(), key=lambda row: row[1]["name"])
        ],
        "estimated_1rm_history": [
            {
                "exercise_id": item.exercise_id,
                "exercise_name": item.exercise.name,
                "e1rm_kg": str(item.e1rm_kg),
                "effective_reps": str(item.effective_reps),
                "measured_on": timezone.localtime(item.measured_on).isoformat(),
            }
            for item in estimates
        ],
    })


@api_endpoint
@api_methods("GET", "PUT", "DELETE")
@api_auth_required
def exercise_preferences(request):
    from account.models import AthleteExercisePreference, Exercise

    queryset = AthleteExercisePreference.objects.filter(user=request.user).select_related("exercise")
    if request.method == "GET":
        return ok({"items": [
            {
                "exercise_id": item.exercise_id,
                "exercise_name": item.exercise.name,
                "status": item.status,
                "reason": item.reason,
                "updated_at": timezone.localtime(item.updated_at).isoformat(),
            }
            for item in queryset
        ]})

    data = request_data(request)
    if request.method == "DELETE":
        _reject_unknown(data, {"exercise_id"})
        exercise_id = parse_int(data.get("exercise_id"), field="exercise_id")
        deleted, _ = queryset.filter(exercise_id=exercise_id).delete()
        return ok({"deleted": bool(deleted)})

    _reject_unknown(data, {"exercise_id", "status", "reason"})
    exercise_id = parse_int(data.get("exercise_id"), field="exercise_id")
    status = str(data.get("status") or "")
    if status not in AthleteExercisePreference.Status.values:
        raise ValidationError({"status": "status must be favorite, avoid, or pain."})
    reason = str(data.get("reason") or "").strip()
    if len(reason) > 500:
        raise ValidationError({"reason": "reason must be at most 500 characters."})
    exercise = get_object_or_404(Exercise, pk=exercise_id)
    preference, _ = AthleteExercisePreference.objects.update_or_create(
        user=request.user,
        exercise=exercise,
        defaults={
            "status": status,
            "source": AthleteExercisePreference.Source.ATHLETE,
            "created_by": request.user,
            "reason": reason,
        },
    )
    return ok({
        "preference": {
            "exercise_id": preference.exercise_id,
            "exercise_name": exercise.name,
            "status": preference.status,
            "reason": preference.reason,
            "updated_at": timezone.localtime(preference.updated_at).isoformat(),
        }
    })


@api_endpoint
@api_methods("GET", "POST")
@api_auth_required
def workout_sessions(request):
    from account.models import WorkoutSession, WorkoutProgram
    from account.services.training_data_service import TrainingDataService

    if request.method == "GET":
        sessions = (
            WorkoutSession.objects.filter(user=request.user)
            .select_related("program", "program_day")
            .prefetch_related("sets", "progression_recommendations")[:50]
        )
        return ok({"items": [TrainingDataService.serialize_session(session) for session in sessions]})
    data = request_data(request)
    if not isinstance(data, dict):
        raise ValidationError({"body": "Request body must be an object."})
    _reject_unknown(data, {"program_id", "program_day_id", *TrainingDataService.SESSION_FIELDS})
    program_id = parse_int(data.get("program_id"), field="program_id")
    day_id = parse_int(data.get("program_day_id"), field="program_day_id")
    program = get_object_or_404(WorkoutProgram, pk=program_id, user=request.user, is_published=True)
    day = get_object_or_404(WorkoutProgramDay, pk=day_id, program=program)
    payload = {key: value for key, value in data.items() if key not in {"program_id", "program_day_id"}}
    session = TrainingDataService.create_session(
        user=request.user,
        program=program,
        day=day,
        payload=payload,
        idempotency_key=request.headers.get("Idempotency-Key"),
    )
    return ok(
        {"session": TrainingDataService.serialize_session(session)},
        status=200 if getattr(session, "_idempotent_replay", False) else 201,
    )

@api_endpoint
@api_methods("POST")
@api_auth_required
def password_change(request):
    data = request_data(request)
    _reject_unknown(data, {"old_password", "new_password"})
    old_password = str(data.get("old_password") or "")
    new_password = str(data.get("new_password") or "")
    validate_strong_password(new_password)
    password_service.change_password(user=request.user, old_password=old_password, new_password=new_password)
    revoke_user_tokens(request.user)
    return ok({"changed": True})

@api_endpoint
@api_methods("POST")
@rate_limit(name="password-reset-request", limit=5, window=900)
def password_reset_request(request):
    data = request_data(request)
    _reject_unknown(data, {"phone"})
    phone = validate_phone_number(data.get("phone"))
    if not User.objects.filter(phone=phone, is_active=True).exists():
        # Avoid account enumeration while keeping the response useful to a
        # legitimate client.
        return ok({"accepted": True, "message": "If the account exists, a verification code was sent."}, status=202)
    result = otp_service.create_otp(phone)
    return ok({"accepted": True, "verification_token": result.token, "expires_in": 300})

@api_endpoint
@api_methods("POST")
@rate_limit(name="password-reset-confirm", limit=10, window=900)
def password_reset_confirm(request):
    data = request_data(request)
    _reject_unknown(data, {"verification_token", "code", "new_password"})
    validate_strong_password(str(data.get("new_password") or ""))
    try:
        code = int(str(data.get("code") or "").strip())
    except ValueError:
        return error("invalid_otp", "The verification code is invalid.", status=400)
    otp = otp_service.validate_otp(token=str(data.get("verification_token") or "").strip(), code=code)
    user = User.objects.filter(phone=otp.phone, is_active=True).first()
    if user is None:
        otp_service.consume_otp(otp)
        return error("invalid_reset", "The password reset request is invalid.", status=400)
    password_service.reset_password(user=user, new_password=str(data.get("new_password")))
    otp_service.consume_otp(otp)
    revoke_user_tokens(user)
    return ok({"reset": True})

@api_endpoint
@api_methods("POST")
@api_auth_required
@rate_limit(name="phone-change-request", limit=5, window=900)
def phone_change_request(request):
    data = request_data(request)
    _reject_unknown(data, {"phone"})
    phone = validate_phone_number(data.get("phone"))
    if User.objects.filter(phone=phone).exclude(pk=request.user.pk).exists():
        return error("phone_unavailable", "This phone number is already in use.", status=409)
    result = otp_service.create_otp(phone)
    return ok({"verification_token": result.token, "expires_in": 300})

@api_endpoint
@api_methods("POST")
@api_auth_required
@rate_limit(name="phone-change-confirm", limit=10, window=900)
def phone_change_confirm(request):
    data = request_data(request)
    _reject_unknown(data, {"verification_token", "code"})
    try:
        code = int(str(data.get("code") or "").strip())
    except ValueError:
        return error("invalid_otp", "The verification code is invalid.", status=400)
    with transaction.atomic():
        otp = otp_service.validate_otp(token=str(data.get("verification_token") or "").strip(), code=code)
        if User.objects.filter(phone=otp.phone).exclude(pk=request.user.pk).exists():
            otp_service.consume_otp(otp)
            return error("phone_unavailable", "This phone number is already in use.", status=409)
        request.user.phone = otp.phone
        request.user.save(update_fields=["phone"])
        otp_service.consume_otp(otp)
    return ok({"user": _serialize_user(request, request.user)})

@api_endpoint
@api_methods("GET")
@api_auth_required
def dashboard(request):
    context = profile_service.get_dashboard_context(request.user)
    programs_count = WorkoutProgram.objects.filter(
        user=request.user, is_published=True
    ).count()
    return ok({
        "cards": context["dashboard_cards"],
        "profile_metrics": context["profile_metrics"],
        "feed": context["dashboard_feed"],
        "counts": {
            "courses": context["learning_courses_count"],
            "orders": context["paid_orders_count"],
            "comments": context["comments_count"],
            "notifications": context["notifications_count"],
            "programs": programs_count,
        },
    })

@api_endpoint
@api_methods("GET")
@api_auth_required
def courses(request):
    return ok({"items": [_serialize_series(request, item, include_description=False) for item in profile_service.get_learning_courses(request.user)]})

@api_endpoint
@api_methods("GET")
@api_auth_required
def notifications(request):
    items = notification_service.get_profile_notifications(request.user, limit=50)
    payload = []
    for item in items:
        key = getattr(item, "key", "") or ""
        identifier = None
        if key.startswith("notification:"):
            suffix = key.split(":", 1)[1]
            identifier = int(suffix) if suffix.isdigit() else None
        payload.append({
            "id": identifier,
            "key": key,
            "title": item.title,
            "message": item.message,
            "created_at": json_value(item.created_at),
            "read": getattr(item, "read", False),
            "global": getattr(item, "is_global", False),
        })
    return ok({"items": payload})

@api_endpoint
@api_methods("POST")
@api_auth_required
def mark_notification_read(request, pk):
    from account.models import Notification

    notification = get_object_or_404(Notification, pk=pk, user=request.user)
    notification.read = True
    notification.save(update_fields=["read"])
    return ok({"read": True})

@api_endpoint
@api_methods("GET", "POST")
@api_auth_required
def coach_request(request):
    if request.method == "GET":
        items = request.user.coach_requests.all()[:20]
        return ok({"items": [{"id": item.pk, "status": item.status, "created_at": json_value(item.created_at), "updated_at": json_value(item.updated_at)} for item in items]})
    pending = coach_request_service.get_pending_for_user(request.user).first()
    allowed = set(CoachRequestForm.Meta.fields) | {"attachments"}
    unknown = set(request_data(request).keys()) - allowed
    if unknown:
        raise ValidationError({"fields": [f"Unsupported fields: {', '.join(sorted(unknown))}"]})
    uploads = request.FILES.getlist("attachments")
    if len(uploads) > 10:
        raise ValidationError({"attachments": "At most 10 attachments may be submitted."})
    for uploaded in uploads:
        validate_coach_request_file(uploaded)
    form = CoachRequestForm(data=request_data(request), files=request.FILES, instance=pending)
    if not form.is_valid():
        raise ValidationError(form.errors)
    item = coach_request_service.save_request(user=request.user, form=form, files=request.FILES.getlist("attachments"))
    return ok({"request": {"id": item.pk, "status": item.status}}, status=201 if pending is None else 200)

@api_endpoint
@api_methods("GET")
@api_auth_required
def programs(request):
    queryset = WorkoutProgram.objects.filter(user=request.user, is_published=True).prefetch_related(
        "days__items__exercise", "days__items__cardio_exercise", "days__items__superset_exercise", "days__items__third_exercise",
        "corrective_items__corrective_exercise",
    )
    return ok({"items": [_serialize_program(request, program) for program in queryset]})

@api_endpoint
@api_methods("POST")
@api_auth_required
def program_performance(request, pk):
    data = request_data(request)
    program = get_object_or_404(WorkoutProgram, pk=pk, user=request.user, is_published=True)
    records = data.get("records", [])
    if not isinstance(records, list) or len(records) > 100:
        raise ValidationError({"records": "records must be an array with at most 100 items."})
    day_id = data.get("day_id")
    day = None
    if day_id is not None:
        day = get_object_or_404(WorkoutProgramDay, pk=day_id, program=program)
    saved = 0
    with transaction.atomic():
        for entry in records:
            if not isinstance(entry, dict):
                raise ValidationError({"records": "Each record must be an object."})
            item = get_object_or_404(WorkoutProgramExercise.objects.select_related("exercise", "superset_exercise", "third_exercise", "day"), pk=entry.get("program_exercise_id"), day__program=program)
            if day is not None and item.day_id != day.pk:
                raise ValidationError({"program_exercise_id": "The exercise does not belong to day_id."})
            exercise_id = parse_int(entry.get("exercise_id"), field="exercise_id")
            if exercise_id not in {item.exercise_id, item.superset_exercise_id, item.third_exercise_id}:
                raise ValidationError({"exercise_id": "The exercise is not part of this program item."})
            mode = str(entry.get("mode") or "")
            if mode not in WorkoutPerformanceRecord.Mode.values:
                raise ValidationError({"mode": "Invalid performance mode."})
            repetitions = str(entry.get("repetitions") or "").strip()[:60]
            set_number = parse_int(entry.get("set_number"), field="set_number")
            if not repetitions or set_number < 1:
                raise ValidationError({"record": "repetitions and a positive set_number are required."})
            try:
                value = Decimal(str(entry.get("value")))
            except (InvalidOperation, TypeError, ValueError):
                raise ValidationError({"value": "value must be a positive number."})
            if value <= 0 or value > Decimal("999999.99"):
                raise ValidationError({"value": "value must be between 0 and 999999.99."})
            record, created = WorkoutPerformanceRecord.objects.select_for_update().get_or_create(
                user=request.user, exercise_id=exercise_id, repetitions=repetitions, mode=mode, set_number=set_number,
                recorded_on=timezone.localdate(),
                defaults={"program": program, "program_exercise": item, "value": value},
            )
            if not created and value > record.value:
                record.value = value
                record.program = program
                record.program_exercise = item
                record.save(update_fields=["value", "program", "program_exercise", "updated_at"])
            saved += 1
        if "difficulty" in data:
            difficulty = parse_int(data.get("difficulty"), field="difficulty")
            if not 0 <= difficulty <= 10:
                raise ValidationError({"difficulty": "difficulty must be between 0 and 10."})
            from account.models import WorkoutProgramFeedback

            WorkoutProgramFeedback.objects.update_or_create(program=program, day=day, defaults={"difficulty": difficulty})
    return ok({"saved_records": saved})

@api_endpoint
@api_methods("GET")
@api_auth_required
def document_download(request, pk):
    document = get_object_or_404(ClientDocument, pk=pk, user=request.user)
    if document.requires_payment and not ClientDocumentPayment.objects.filter(document=document, user=request.user, status=ClientDocumentPayment.Status.PAID).exists():
        return error("payment_required", "This document requires payment.", status=403)
    try:
        file = open_client_file(document.file)
    except FileNotFoundError:
        return error("file_unavailable", "The document file is unavailable.", status=404)
    response = FileResponse(file, as_attachment=True, filename=os.path.basename(document.file.name))
    response["Cache-Control"] = "private, no-store"
    return response

@api_endpoint
@api_methods("POST")
@api_auth_required
def document_payment(request, pk):
    document = get_object_or_404(ClientDocument, pk=pk, user=request.user)
    if not document.requires_payment or document.price <= 0:
        return error("payment_not_required", "This document does not require payment.", status=400)
    result = access_payment_service.initiate_document(
        document_id=document.pk,
        user=request.user,
        callback_url=build_payment_callback_url(request, url_name="api:document_payment_verify"),
    )
    if result.already_paid:
        return ok({"document_id": document.pk, "already_paid": True})
    return ok({"document_id": document.pk, "payment_id": result.payment_id, "redirect_url": result.redirect_url})
