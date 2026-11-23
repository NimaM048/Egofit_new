"""Service instances shared by staff portal features."""

from __future__ import annotations

from account.services.admin_portal_service import AdminPortalService
from account.services.coach_application_service import CoachApplicationService
from account.services.exercise_suggestion_service import ExerciseSuggestionService
from account.services.health_alert_service import HealthAlertService
from account.services.mood_service import MoodService


admin_portal_service = AdminPortalService()


coach_application_service = CoachApplicationService()


exercise_suggestion_service = ExerciseSuggestionService()


health_alert_service = HealthAlertService()


mood_service = MoodService()
