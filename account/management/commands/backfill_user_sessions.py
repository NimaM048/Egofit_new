"""Re-derive device/browser/OS/bot metadata for stored user sessions."""
from __future__ import annotations

from django.core.management.base import BaseCommand

from account.models import UserSession


class Command(BaseCommand):
    help = "Parse stored user-agent strings and fill session device/browser/OS/bot fields."

    def add_arguments(self, parser):
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            help="Number of sessions fetched per query batch.",
        )

    def handle(self, *args, **options):
        batch_size = max(1, options["batch_size"])
        updated = 0
        checked = 0

        queryset = UserSession.objects.exclude(user_agent="").only(
            "id",
            "user_agent",
            "device_type",
            "browser",
            "os",
            "is_bot",
        )
        for session in queryset.iterator(chunk_size=batch_size):
            checked += 1
            if not session.apply_user_agent_metadata():
                continue
            UserSession.objects.filter(pk=session.pk).update(
                device_type=session.device_type,
                browser=session.browser,
                os=session.os,
                is_bot=session.is_bot,
            )
            updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Session metadata refreshed: {updated} updated out of {checked} checked."
            )
        )
