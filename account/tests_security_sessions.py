from __future__ import annotations

from datetime import timedelta

from django.core.cache import cache
from django.core.management import call_command
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.utils import timezone

from account.models import BlockedIP, User, UserSession
from account.security_middleware import AccountSecurityMiddleware, blocked_ip_cache_key


DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
GOOGLEBOT_UA = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"


class UserSessionEnrichmentTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            fullname="Session User",
            phone="09121110000",
            password="test-pass-123",
        )

    def test_session_parses_device_browser_and_os(self):
        session = UserSession.objects.create(
            user=self.user,
            session_key="desktop-session",
            ip_address="127.0.0.1",
            user_agent=DESKTOP_UA,
        )

        self.assertEqual(session.device_type, "desktop")
        self.assertEqual(session.browser, "Chrome")
        self.assertEqual(session.os, "Windows")
        self.assertFalse(session.is_bot)

    def test_session_flags_known_bot_user_agents(self):
        session = UserSession.objects.create(
            user=self.user,
            session_key="bot-session",
            ip_address="127.0.0.2",
            user_agent=GOOGLEBOT_UA,
        )

        self.assertTrue(session.is_bot)


class BlockedIPMiddlewareTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_blocked_ip_gets_forbidden_response(self):
        BlockedIP.objects.create(ip_address="10.0.0.5", reason="abuse")
        middleware = AccountSecurityMiddleware(lambda request: HttpResponse("ok"))
        request = self.factory.get("/", REMOTE_ADDR="10.0.0.5")
        request.user = type("Anonymous", (), {"is_authenticated": False})()

        response = middleware(request)

        self.assertEqual(response.status_code, 403)

    def test_unblocked_ip_passes_through(self):
        middleware = AccountSecurityMiddleware(lambda request: HttpResponse("ok"))
        request = self.factory.get("/", REMOTE_ADDR="10.0.0.9")
        request.user = type("Anonymous", (), {"is_authenticated": False})()

        response = middleware(request)

        self.assertEqual(response.status_code, 200)


class UserSessionAdminActionTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            fullname="Panel Admin",
            phone="09121110001",
            password="test-pass-123",
            is_admin=True,
        )
        self.user = User.objects.create_user(
            fullname="Session Owner",
            phone="09121110002",
            password="test-pass-123",
        )
        self.session = UserSession.objects.create(
            user=self.user,
            session_key="admin-action-session",
            ip_address="10.10.10.10",
            user_agent=DESKTOP_UA,
        )
        self.client.force_login(self.admin)
        self.changelist_url = "/Mpannel/account/usersession/"

    def test_changelist_is_available(self):
        response = self.client.get(self.changelist_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "block_sessions")

    def test_block_action_blocks_ip_and_invalidates_cache(self):
        cache.set(blocked_ip_cache_key("10.10.10.10"), False, 300)

        response = self.client.post(
            self.changelist_url,
            {
                "action": "block_sessions",
                "_selected_action": [str(self.session.pk)],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        blocked_ip = BlockedIP.objects.get(ip_address="10.10.10.10")
        self.assertTrue(blocked_ip.is_active)
        self.assertIsNone(blocked_ip.expires_at)
        self.session.refresh_from_db()
        self.assertTrue(self.session.is_blocked)
        self.assertIsNone(cache.get(blocked_ip_cache_key("10.10.10.10")))

    def test_block_action_clears_expired_expiry(self):
        BlockedIP.objects.create(
            ip_address="10.10.10.10",
            is_active=False,
            expires_at=timezone.now() - timedelta(days=1),
        )

        self.client.post(
            self.changelist_url,
            {
                "action": "block_sessions",
                "_selected_action": [str(self.session.pk)],
            },
        )

        blocked_ip = BlockedIP.objects.get(ip_address="10.10.10.10")
        self.assertTrue(blocked_ip.is_active)
        self.assertIsNone(blocked_ip.expires_at)

    def test_unblock_action_reactivates_sessions(self):
        BlockedIP.objects.create(ip_address="10.10.10.10", is_active=True)
        UserSession.objects.filter(pk=self.session.pk).update(is_blocked=True)
        cache.set(blocked_ip_cache_key("10.10.10.10"), True, 300)

        self.client.post(
            self.changelist_url,
            {
                "action": "unblock_sessions",
                "_selected_action": [str(self.session.pk)],
            },
        )

        self.assertFalse(BlockedIP.objects.get(ip_address="10.10.10.10").is_active)
        self.session.refresh_from_db()
        self.assertFalse(self.session.is_blocked)
        self.assertIsNone(cache.get(blocked_ip_cache_key("10.10.10.10")))


class SessionMetadataBackfillTests(TestCase):
    def test_backfill_fills_missing_metadata(self):
        user = User.objects.create_user(
            fullname="Legacy Session",
            phone="09121110003",
            password="test-pass-123",
        )
        session = UserSession.objects.create(
            user=user,
            session_key="legacy-session",
            ip_address="127.0.0.3",
            user_agent=DESKTOP_UA,
        )
        UserSession.objects.filter(pk=session.pk).update(
            device_type="",
            browser="",
            os="",
            is_bot=False,
        )

        call_command("backfill_user_sessions", verbosity=0)

        session.refresh_from_db()
        self.assertEqual(session.device_type, "desktop")
        self.assertEqual(session.browser, "Chrome")
        self.assertEqual(session.os, "Windows")
