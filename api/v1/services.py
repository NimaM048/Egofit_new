"""Service instances shared by multiple API features."""

from account.services import OTPService, ProfileService
from account.services.access_payment_service import AccessPaymentService


otp_service = OTPService()
profile_service = ProfileService()
access_payment_service = AccessPaymentService()
