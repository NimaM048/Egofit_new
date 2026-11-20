from __future__ import annotations

from account.models import AthleteHealthAlert


class HealthAlertSelector:
    @staticmethod
    def for_recipient(user, *, limit: int = 30):
        queryset = AthleteHealthAlert.objects.filter(recipient=user)
        if user.is_coach and not user.is_admin:
            queryset = queryset.filter(athlete__coach=user)
        return queryset.select_related("athlete", "recipient", "athlete__coach").order_by(
            "-created_at", "-id"
        )[:limit]
