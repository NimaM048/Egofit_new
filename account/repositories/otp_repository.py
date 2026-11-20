from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from account.constants import OTP_EXPIRATION_MINUTES
from account.models import Otp
from account.otp_security import encrypt_otp_code, otp_code_digest


class OTPRepository:
    @transaction.atomic
    def create(
        self,
        *,
        phone: str,
        code: int,
        token: str,
        ip_address: str | None = None,
        user_agent: str = "",
    ) -> Otp:
        return Otp.objects.create(
            phone=phone,
            token=token,
            code_hash=otp_code_digest(token=token, code=code),
            code_encrypted=encrypt_otp_code(token=token, code=code),
            expires_at=timezone.now() + timedelta(minutes=OTP_EXPIRATION_MINUTES),
            ip_address=ip_address or None,
            user_agent=(user_agent or "")[:255],
        )

    def delete_existing_for_phone(self, phone: str) -> int:
        deleted_count, _ = Otp.objects.filter(phone=phone).delete()
        return deleted_count

    def get_by_token_for_update(self, token: str) -> Otp:
        return Otp.objects.select_for_update().get(token=token)

    def get_by_token(self, token: str) -> Otp:
        return Otp.objects.get(token=token)

    def delete(self, otp: Otp) -> None:
        otp.delete()

    def get_latest_for_phone(self, phone: str) -> Otp | None:
        return Otp.objects.filter(phone=phone).order_by("-created_at").first()

    def delete_expired(self) -> int:
        now = timezone.now()
        fallback_created_before = now - timedelta(minutes=OTP_EXPIRATION_MINUTES)
        deleted_count, _ = Otp.objects.filter(
            Q(expires_at__lte=now)
            | Q(expires_at__isnull=True, created_at__lte=fallback_created_before)
            | Q(expires_at__isnull=True, created_at__isnull=True, exprision_date__lte=fallback_created_before)
        ).delete()
        return deleted_count
