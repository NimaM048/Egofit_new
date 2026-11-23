"""Compatibility imports for the workout portal views."""

from .overview import ProfileWorkoutProgramsView
from .performance import ProfileWorkoutProgramPerformanceView
from .details import ProfileWorkoutProgramPdfView, ProfileWorkoutMovementView
from .support import _has_workout_program_access


__all__ = [
    "ProfileWorkoutMovementView",
    "ProfileWorkoutProgramPdfView",
    "ProfileWorkoutProgramPerformanceView",
    "ProfileWorkoutProgramsView",
]
