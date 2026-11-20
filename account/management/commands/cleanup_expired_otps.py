from django.core.management.base import BaseCommand

from account.repositories.otp_repository import OTPRepository


class Command(BaseCommand):
    help = "Delete expired one-time passwords. Also run automatically during OTP issuance."

    def handle(self, *args, **options):
        deleted = OTPRepository().delete_expired()
        self.stdout.write(self.style.SUCCESS(f"Deleted {deleted} expired OTP record(s)."))
