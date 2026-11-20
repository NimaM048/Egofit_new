from __future__ import annotations

from sport_shop.task_queue import shared_task


@shared_task(name="account.cleanup_expired_otps")
def cleanup_expired_otps() -> int:
    from account.repositories.otp_repository import OTPRepository

    return OTPRepository().delete_expired()
