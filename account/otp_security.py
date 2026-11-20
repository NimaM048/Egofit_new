from __future__ import annotations

from base64 import urlsafe_b64encode
from hashlib import sha256
import hmac

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.utils.crypto import salted_hmac


def _otp_cipher() -> Fernet:
    key = urlsafe_b64encode(sha256(settings.SECRET_KEY.encode("utf-8")).digest())
    return Fernet(key)


def otp_code_digest(*, token: str, code: int | str) -> str:
    """Return a keyed digest so short OTPs cannot be recovered from the DB alone."""
    return salted_hmac(
        "account.otp-code",
        f"{token}:{code}",
        secret=settings.SECRET_KEY,
        algorithm="sha256",
    ).hexdigest()


def encrypt_otp_code(*, token: str, code: int | str) -> str:
    """Encrypt an OTP for the staff-only admin display while keeping the digest for checks."""
    payload = f"{token}:{code}".encode("utf-8")
    return _otp_cipher().encrypt(payload).decode("ascii")


def decrypt_otp_code(*, token: str, encrypted_code: str) -> str:
    """Return an OTP only when its encrypted value belongs to the given token."""
    if not encrypted_code:
        return ""
    try:
        payload = _otp_cipher().decrypt(encrypted_code.encode("ascii")).decode("utf-8")
        stored_token, code = payload.rsplit(":", 1)
    except (InvalidToken, UnicodeDecodeError, ValueError):
        return ""
    if not hmac.compare_digest(stored_token, token or ""):
        return ""
    return code.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
