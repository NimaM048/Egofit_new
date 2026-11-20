from __future__ import annotations

from django.utils import timezone

from account.models import UserSession, User


class SessionRepository:
    def upsert_login_session(
        self,
        *,
        user: User,
        session_key: str,
        ip_address: str,
        user_agent: str,
        accept_language: str = "",
        referrer: str = "",
    ) -> UserSession:
        session, created = UserSession.objects.update_or_create(
            user=user,
            session_key=session_key,
            defaults={
                "ip_address": ip_address,
                "user_agent": user_agent[:255],
                "last_seen_at": timezone.now(),
                "accept_language": (accept_language or "")[:120],
                "referrer": (referrer or "")[:500],
            },
        )
        if not created:
            session.login_count = (session.login_count or 0) + 1
            session.save(update_fields=["login_count"])
        return session

