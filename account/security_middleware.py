from __future__ import annotations

import logging

from django.core.cache import cache
from django.db.models import Q
from django.http import HttpResponseForbidden
from django.utils import timezone

from account.models import BlockedIP, UserSession


logger = logging.getLogger(__name__)

BLOCKED_IP_CACHE_TTL = 300
SESSION_TOUCH_THROTTLE = 300
BLOCKED_IP_CACHE_KEY = "account:blocked-ip:{ip_address}"


def client_ip(request) -> str:
    return (request.META.get("REMOTE_ADDR") or "").strip()


def blocked_ip_cache_key(ip_address: str) -> str:
    return BLOCKED_IP_CACHE_KEY.format(ip_address=ip_address)


def invalidate_blocked_ip_cache(ip_address: str | None = None) -> None:
    """Drop cached block state so admin actions take effect immediately."""
    if not ip_address:
        return
    cache.delete(blocked_ip_cache_key(ip_address))


class AccountSecurityMiddleware:
    """Reject blocked IPs and keep session activity timestamps fresh."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        ip_address = client_ip(request)
        is_staff = bool(getattr(getattr(request, "user", None), "is_staff", False))
        if ip_address and not is_staff and self._is_blocked(ip_address):
            logger.warning("Blocked IP attempted access: %s %s", ip_address, request.path)
            return HttpResponseForbidden("دسترسی شما به این سایت محدود شده است.")

        response = self.get_response(request)
        self._touch_session(request)
        return response

    def _is_blocked(self, ip_address: str) -> bool:
        cache_key = blocked_ip_cache_key(ip_address)
        cached = cache.get(cache_key)
        if cached is not None:
            return bool(cached)

        now = timezone.now()
        block = (
            BlockedIP.objects.filter(ip_address=ip_address, is_active=True)
            .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
            .values("expires_at")
            .first()
        )
        blocked = block is not None
        cache_ttl = BLOCKED_IP_CACHE_TTL
        if blocked and block["expires_at"]:
            remaining = int((block["expires_at"] - now).total_seconds())
            cache_ttl = min(cache_ttl, max(1, remaining))
        cache.set(cache_key, blocked, cache_ttl)
        return blocked

    def _touch_session(self, request) -> None:
        user = getattr(request, "user", None)
        if not user or not getattr(user, "is_authenticated", False):
            return
        session_key = getattr(getattr(request, "session", None), "session_key", None)
        if not session_key:
            return

        cache_key = f"account:session-touch:{session_key}"
        if cache.get(cache_key):
            return
        cache.set(cache_key, True, SESSION_TOUCH_THROTTLE)
        UserSession.objects.filter(session_key=session_key).update(last_seen_at=timezone.now())
