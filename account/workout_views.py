"""Compatibility imports; use account.workouts.views for new code."""

from account.workouts.views import (
    ProfileWorkoutMovementView,
    ProfileWorkoutProgramPdfView,
    ProfileWorkoutProgramPerformanceView,
    ProfileWorkoutProgramsView,
    _has_workout_program_access,
)


__all__ = [
    "ProfileWorkoutMovementView",
    "ProfileWorkoutProgramPdfView",
    "ProfileWorkoutProgramPerformanceView",
    "ProfileWorkoutProgramsView",
]
