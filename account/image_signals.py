from __future__ import annotations

from home.image_signals import connect_image_optimizer


def connect_account_image_optimizer() -> None:
    from account.models import ClientMedia, Muscle, User, WorkoutProgram

    connect_image_optimizer((User, ClientMedia, Muscle, WorkoutProgram))
