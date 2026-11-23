"""Program access and the common workout query service."""

from __future__ import annotations

from account.models import WorkoutProgramPayment
from account.services.gym_program_service import GymProgramService


gym_program_service = GymProgramService()


def _has_workout_program_access(user, program) -> bool:
    return bool(
        program
        and (
            not program.requires_payment
            or WorkoutProgramPayment.objects.filter(
                program=program,
                user=user,
                status=WorkoutProgramPayment.Status.PAID,
            ).exists()
        )
    )
