from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import hmac
from secrets import randbelow
from uuid import uuid4

from django.db import transaction
from django.utils import timezone

from account.constants import OTP_CODE_LENGTH, OTP_EXPIRATION_MINUTES, OTP_REQUEST_COOLDOWN_SECONDS
from account.exceptions import ExpiredOtpException, InvalidOtpException, OTPRateLimitExceededException, SMSProviderException
from account.models import Otp, OtpAuditLog
from account.otp_security import otp_code_digest
from account.tasks.dispatch import dispatch_task
from account.tasks.sms_tasks import send_verification_sms
from account.repositories.otp_repository import OTPRepository


OTP_MAX_ATTEMPTS = 5
OTP_LOCKOUT_MINUTES = 15


@dataclass(frozen=True)
class OTPCreationResult:
    token: str
    code: int


@dataclass(frozen=True)
class OTPVerificationStatus:
    phone: str
    expires_in_seconds: int
    resend_cooldown_seconds: int
    can_resend: bool


class OTPService:
    def __init__(self, repository: OTPRepository | None = None, sms_service=None):
        self.repository = repository or OTPRepository()
        self.sms_service = sms_service

    def _generate_code(self) -> int:
        lower_bound = 10 ** (OTP_CODE_LENGTH - 1)
        upper_bound = (10**OTP_CODE_LENGTH) - 1
        return lower_bound + randbelow(upper_bound - lower_bound + 1)

    @staticmethod
    def _expiration_at(otp):
        created_at = otp.created_at or otp.exprision_date or timezone.now()
        return otp.expires_at or (created_at + timedelta(minutes=OTP_EXPIRATION_MINUTES))

    def _seconds_since_latest_otp(self, phone: str) -> int | None:
        latest_otp = self.repository.get_latest_for_phone(phone)
        if latest_otp is None:
            return None
        created_at = latest_otp.created_at or latest_otp.exprision_date
        return int((timezone.now() - created_at).total_seconds())

    def _can_request_otp(self, phone: str) -> bool:
        if OTP_REQUEST_COOLDOWN_SECONDS <= 0:
            return True
        elapsed = self._seconds_since_latest_otp(phone)
        if elapsed is None:
            return True
        return elapsed >= OTP_REQUEST_COOLDOWN_SECONDS

    def _resend_cooldown_remaining(self, phone: str) -> int:
        if OTP_REQUEST_COOLDOWN_SECONDS <= 0:
            return 0
        elapsed = self._seconds_since_latest_otp(phone)
        if elapsed is None:
            return 0
        return max(0, OTP_REQUEST_COOLDOWN_SECONDS - elapsed)

    def get_verification_status(self, token: str) -> OTPVerificationStatus:
        otp = self.repository.get_by_token(token)
        now = timezone.now()
        expiration_at = self._expiration_at(otp)
        expires_in_seconds = max(0, int((expiration_at - now).total_seconds()))
        resend_cooldown_seconds = self._resend_cooldown_remaining(otp.phone)
        if otp.locked_until and otp.locked_until > now:
            resend_cooldown_seconds = max(
                resend_cooldown_seconds,
                int((otp.locked_until - now).total_seconds()),
            )
        return OTPVerificationStatus(
            phone=otp.phone,
            expires_in_seconds=expires_in_seconds,
            resend_cooldown_seconds=resend_cooldown_seconds,
            can_resend=resend_cooldown_seconds == 0,
        )

    def create_otp(self, phone: str, *, ip_address: str | None = None, user_agent: str = "") -> OTPCreationResult:
        self.repository.delete_expired()
        if not self._can_request_otp(phone):
            remaining = self._resend_cooldown_remaining(phone)
            minutes = max(1, (remaining + 59) // 60)
            self._write_audit(
                phone=phone,
                event=OtpAuditLog.Event.RATE_LIMITED,
                ip_address=ip_address,
                user_agent=user_agent,
            )
            raise OTPRateLimitExceededException(f"لطفاً {minutes} دقیقه دیگر برای دریافت کد جدید صبر کنید.")

        with transaction.atomic():
            self.repository.delete_existing_for_phone(phone)
            token = uuid4().hex
            code = self._generate_code()
            self.repository.create(
                phone=phone,
                code=code,
                token=token,
                ip_address=ip_address,
                user_agent=user_agent,
            )
            self._write_audit(
                phone=phone,
                event=OtpAuditLog.Event.REQUESTED,
                ip_address=ip_address,
                user_agent=user_agent,
            )

        if self.sms_service is not None:
            try:
                self.sms_service.send_verification_sms(phone, code)
            except SMSProviderException:
                self.repository.delete_existing_for_phone(phone)
                self._write_audit(
                    phone=phone,
                    event=OtpAuditLog.Event.DELIVERY_FAILED,
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
                raise
        else:
            dispatch_task(send_verification_sms, phone, code)

        self._write_audit(
            phone=phone,
            event=OtpAuditLog.Event.SENT,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        return OTPCreationResult(token=token, code=code)

    def validate_otp(
        self,
        *,
        token: str,
        code: int,
        consume: bool = False,
        ip_address: str | None = None,
        user_agent: str = "",
    ):
        otp = None
        error = None
        now = timezone.now()

        # Commit failed-attempt counters and audit rows before raising. Raising
        # inside this atomic block would roll both security records back.
        with transaction.atomic():
            try:
                otp = self.repository.get_by_token_for_update(token)
            except Otp.DoesNotExist:
                self._write_audit(
                    phone="",
                    event=OtpAuditLog.Event.INVALID,
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
                error = InvalidOtpException("کد وارد شده نادرست یا منقضی شده است.")
            else:
                expiration_at = self._expiration_at(otp)
                if otp.locked_until and otp.locked_until > now:
                    self._write_audit(
                        phone=otp.phone,
                        event=OtpAuditLog.Event.LOCKED,
                        ip_address=ip_address or otp.ip_address,
                        user_agent=user_agent or otp.user_agent,
                    )
                    error = InvalidOtpException("به‌دلیل تلاش‌های ناموفق، ورود موقتاً قفل شده است.")
                elif now > expiration_at:
                    self.repository.delete(otp)
                    self._write_audit(
                        phone=otp.phone,
                        event=OtpAuditLog.Event.EXPIRED,
                        ip_address=ip_address or otp.ip_address,
                        user_agent=user_agent or otp.user_agent,
                    )
                    error = ExpiredOtpException("کد وارد شده منقضی شده است.")
                elif not hmac.compare_digest(
                    otp.code_hash,
                    otp_code_digest(token=token, code=code),
                ):
                    otp.attempts = (otp.attempts or 0) + 1
                    if otp.attempts >= OTP_MAX_ATTEMPTS:
                        otp.locked_until = now + timedelta(minutes=OTP_LOCKOUT_MINUTES)
                        event = OtpAuditLog.Event.LOCKED
                    else:
                        event = OtpAuditLog.Event.INVALID
                    otp.save(update_fields=["attempts", "locked_until"])
                    self._write_audit(
                        phone=otp.phone,
                        event=event,
                        ip_address=ip_address or otp.ip_address,
                        user_agent=user_agent or otp.user_agent,
                    )
                    error = InvalidOtpException("کد وارد شده نادرست یا منقضی شده است.")
                else:
                    self._write_audit(
                        phone=otp.phone,
                        event=OtpAuditLog.Event.VERIFIED,
                        ip_address=ip_address or otp.ip_address,
                        user_agent=user_agent or otp.user_agent,
                    )
                    if consume:
                        self.repository.delete(otp)

        if error:
            raise error
        return otp

    @staticmethod
    def _write_audit(*, phone: str, event: str, ip_address: str | None, user_agent: str) -> None:
        OtpAuditLog.objects.create(
            phone=(phone or "")[:11],
            ip_address=ip_address or None,
            user_agent=(user_agent or "")[:255],
            event=event,
        )

    def consume_otp(self, otp) -> None:
        self.repository.delete(otp)

    def resend_otp(
        self,
        *,
        token: str,
        ip_address: str | None = None,
        user_agent: str = "",
    ) -> str:
        otp = self.repository.get_by_token(token)
        return self.create_otp(otp.phone, ip_address=ip_address, user_agent=user_agent).token
