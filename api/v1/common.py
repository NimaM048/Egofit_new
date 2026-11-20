from __future__ import annotations

import os
from decimal import Decimal, InvalidOperation

from django.contrib.auth import authenticate
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from account.froms import BirthDateForm, BloodGroupForm, CoachRequestForm, HeightForm, WeightForm, validate_coach_request_file
from account.models import (
    ClientDocument,
    ClientDocumentPayment,
    CoachRequest,
    User,
    WorkoutPerformanceRecord,
    WorkoutProgram,
    WorkoutProgramDay,
    WorkoutProgramExercise,
)
from account.services import AuthService, NotificationService, OTPService, PasswordService, ProfileService
from account.services.coach_request_service import CoachRequestService
from account.services.access_payment_service import AccessPaymentService
from account.validators import validate_fullname, validate_image_upload, validate_phone_number, validate_strong_password
from api.auth import _hash_token, issue_token_pair, revoke_user_tokens, rotate_token_pair
from api.decorators import api_auth_required, api_endpoint, api_methods
from api.limits import rate_limit
from api.models import ApiToken
from api.responses import error, ok
from api.utils import absolute_file_url, json_value, parse_int, request_data
from cart.card_models import Cart, UserCart
from cart.exceptions import PaymentProviderException, PaymentVerificationException
from cart.providers.zarinpal_provider import ZarinPalPaymentProvider
from cart.models import Order
from cart.services import PaymentService, apply_discount, build_order_from_cart
from cart.selectors.order_selector import OrderSelector
from cart.zarinpal import build_payment_callback_url
from home.forms import CommentSectionForm, ReplyForm
from home.models import ArticleBlogModel, CommentSectionModel, Episode, Reply, SeriesModel
from home.selectors.article_selector import ArticleSelector
from home.selectors.comment_selector import CommentSelector
from home.selectors.course_selector import CourseSelector
from home.services import CommentService, SearchService, SeriesService, VideoService


auth_service = AuthService()
otp_service = OTPService()
profile_service = ProfileService()
password_service = PasswordService()
comment_service = CommentService()
coach_request_service = CoachRequestService()
notification_service = NotificationService()
payment_service = PaymentService()
access_payment_service = AccessPaymentService()
series_service = SeriesService()


def _user_data(request, user):
    return {
        "id": user.pk,
        "is_admin": bool(user.is_admin),
        "is_coach": bool(getattr(user, "is_coach", False)),
        "fullname": user.fullname,
        "display_name": user.display_name or user.fullname or "",
        "first_name": user.first_name,
        "last_name": user.last_name,
        "email": user.email,
        "phone": user.phone,
        "biography": user.biography,
        "birth_date_jalali": user.birth_date_jalali,
        "weight_kg": json_value(user.weight_kg),
        "height_cm": json_value(user.height_cm),
        "blood_group": user.blood_group,
        "gender": user.gender,
        "activity_level": user.activity_level,
        "profile_picture": absolute_file_url(request, user.profile_picture),
    }


def _serialize_user(request, user):
    return _user_data(request, user)


def _serialize_series(request, series, *, include_description=True):
    payload = {
        "id": series.pk,
        "title": series.title,
        "is_published": series.is_published,
        "free": series.free,
        "completed": series.is_compeleted,
        "seasons": series.seseaons,
        "hours": series.hours,
        "main_price": series.main_price or "",
        "discount_price": series.discount_price or "",
        "image": absolute_file_url(request, series.image),
        "author": {"id": series.author_id, "fullname": series.author.fullname} if series.author_id else None,
        "category": {"id": series.language_kinds_id, "title": series.language_kinds.title} if series.language_kinds_id else None,
    }
    if include_description:
        payload.update({"description": series.description or "", "introduction": series.introdution_course or ""})
    return payload


def _serialize_article(request, article, *, detail=False):
    payload = {
        "id": article.pk,
        "slug": article.slug,
        "title": article.title,
        "reading_time": article.reading_time,
        "excerpt": article.article_excerpt,
        "image": absolute_file_url(request, article.image),
        "author": {"id": article.author_id, "fullname": article.author.fullname} if article.author_id else None,
    }
    if detail:
        payload.update({"description": article.article_description, "built_in": article.built_in})
    return payload


def _serialize_comment(comment):
    return {
        "id": comment.pk,
        "text": comment.text,
        "created_at": json_value(comment.created_at),
        "author": {"id": comment.user_id, "fullname": comment.user.fullname} if comment.user_id else None,
        "replies": [
            {
                "id": reply.pk,
                "text": reply.text,
                "created_at": json_value(reply.created_at),
                "author": {"id": reply.user_id, "fullname": reply.user.fullname} if reply.user_id else None,
            }
            for reply in getattr(comment, "active_replies", [])
        ],
    }


def _serialize_order(order):
    return {
        "id": order.pk,
        "order_number": order.order_number,
        "subtotal_price": order.subtotal_price,
        "discount_amount": order.discount_amount,
        "total_price": order.total_price,
        "is_paid": order.is_paid,
        "status": order.status,
        "created_at": json_value(order.created_at),
        "paid_at": json_value(order.paid_at),
        "items": [
            {"product_id": item.product_id, "title": item.product.title, "price": item.price}
            for item in order.items.all()
        ],
    }


def _serialize_program(request, program):
    from account.services.gym_program_service import GymProgramService
    from account.services.training_periodization_service import TrainingPeriodizationService

    def serialize_movement(movement, *, cardio=False):
        if movement is None:
            return None
        media = {
            "primary": absolute_file_url(request, getattr(movement, "media", None) or getattr(movement, "image", None)),
            "videos": [absolute_file_url(request, item) for item in getattr(movement, "video_files", [])],
        }
        return {"id": movement.pk, "name": movement.name, "type": "cardio" if cardio else "strength", "media": media}

    training_week = TrainingPeriodizationService.current_week(program)
    periodization_plan = (program.generation_metadata or {}).get("periodization") or {}

    def serialize_program_item(item):
        active_sets = TrainingPeriodizationService.active_sets(program, item.sets, item=item)
        sets_min, sets_max = GymProgramService._range_from_text(active_sets)
        in_deload = training_week["is_deload"]
        return {
            "id": item.pk,
            "exercise": serialize_movement(item.movement, cardio=bool(item.cardio_exercise_id)),
            "superset_exercise": serialize_movement(item.superset_exercise) if item.superset_exercise_id else None,
            "third_exercise": serialize_movement(item.third_exercise) if item.third_exercise_id else None,
            "sets": active_sets,
            "base_sets": item.sets,
            "reps": item.reps,
            "rest": item.rest,
            "note": item.note,
            "order": item.order,
            "selection_reason": item.selection_reason,
            "selection_score": item.selection_score,
            "prescription": {
                "sets_min": sets_min,
                "sets_max": sets_max,
                "reps_min": item.reps_min,
                "reps_max": item.reps_max,
                "load_kg": json_value(item.target_load_kg),
                "load_percent_1rm": json_value(item.target_load_percent_1rm),
                "rpe": item.target_rpe,
                "rir": item.target_rir,
                "rest_seconds_min": item.rest_seconds_min,
                "rest_seconds_max": item.rest_seconds_max,
                "cardio_duration_seconds": item.cardio_duration_seconds,
                "cardio_interval_count": item.cardio_interval_count,
                "cardio_work_seconds": item.cardio_work_seconds,
                "cardio_recovery_seconds": item.cardio_recovery_seconds,
                "target_heart_rate_min": item.target_heart_rate_min,
                "target_heart_rate_max": item.target_heart_rate_max,
            },
            "warmup_sets": item.warmup_sets or [],
            "previous_performance": item.previous_performance_snapshot or {},
            "weekly_set_targets": item.weekly_set_targets or {},
        }

    return {
        "id": program.pk,
        "title": program.title,
        "revision": program.revision,
        "algorithm_version": program.algorithm_version,
        "generation_seed": program.generation_seed,
        "program_type": program.program_type,
        "is_published": program.is_published,
        "requires_payment": program.requires_payment,
        "price": program.price,
        "start_date": json_value(program.start_date),
        "end_date": json_value(program.end_date),
        "notes": program.notes,
        "supplements_note": program.supplements_note,
        "warmup_notes": program.warmup_notes,
        "cooldown_notes": program.cooldown_notes,
        "training_cycle": {
            **training_week,
            "cycle_weeks": periodization_plan.get("cycle_weeks", 4),
            "deload_description": periodization_plan.get("description", ""),
        },
        "days": [
            {
                "id": day.pk,
                "name": day.name,
                "order": day.order,
                "notes": day.notes,
                "scheduled_weekday": day.scheduled_weekday,
                "estimated_duration_minutes": day.estimated_duration_minutes,
                "exercises": [serialize_program_item(item) for item in day.items.all()],
            }
            for day in program.days.all()
        ],
        "correctives": [
            {
                "id": item.pk,
                "exercise": {"id": item.corrective_exercise_id, "name": item.corrective_exercise.name},
                "phase": item.phase,
                "sets": item.sets,
                "reps": item.reps,
                "note": item.note,
                "media": {
                    "primary": absolute_file_url(request, item.corrective_exercise.media),
                    "videos": [absolute_file_url(request, media) for media in item.corrective_exercise.video_files],
                },
            }
            for item in program.corrective_items.select_related("corrective_exercise").all()
        ],
    }


def _reject_unknown(data, allowed):
    unknown = set(data.keys()) - set(allowed)
    if unknown:
        raise ValidationError({"fields": [f"Unsupported fields: {', '.join(sorted(unknown))}"]})



__all__ = [name for name in globals() if not name.startswith("__")]
