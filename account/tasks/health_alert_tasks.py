from __future__ import annotations

import json
import logging

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from account.models import (
    AthleteHealthAlert,
    DailyHeartRateEntry,
    DailyMoodEntry,
    MoodReminderSubscription,
    User,
)
from account.services.health_alert_service import HealthAlertService, _two_months_before
from sport_shop.task_queue import shared_task


logger = logging.getLogger(__name__)


@shared_task(name="account.scan_athlete_health_trends")
def scan_athlete_health_trends() -> int:
    """Check existing two-month trends daily, including data recorded before deployment."""
    start_date = _two_months_before(timezone.localdate())
    mood_user_ids = DailyMoodEntry.objects.filter(entry_date__gte=start_date).values_list("user_id", flat=True)
    heart_user_ids = DailyHeartRateEntry.objects.filter(entry_date__gte=start_date).values_list("user_id", flat=True)
    athletes = (
        User.objects.filter(is_active=True, is_admin=False, is_coach=False)
        .filter(Q(pk__in=mood_user_ids) | Q(pk__in=heart_user_ids))
        .select_related("coach")
    )
    service = HealthAlertService()
    processed = 0
    for athlete in athletes.iterator(chunk_size=500):
        service.evaluate_user(athlete)
        processed += 1
    return processed


@shared_task(name="account.send_health_alert_push")
def send_health_alert_push(alert_id: int) -> int:
    private_key = str(getattr(settings, "WEBPUSH_VAPID_PRIVATE_KEY", "") or "").replace("\\n", "\n").strip()
    if not private_key:
        return 0

    from pywebpush import WebPushException, webpush

    alert = (
        AthleteHealthAlert.objects.select_related("recipient", "athlete")
        .filter(pk=alert_id)
        .first()
    )
    if alert is None:
        return 0

    athlete = alert.athlete
    athlete_name = " ".join(part for part in (athlete.first_name, athlete.last_name) if part).strip()
    athlete_name = athlete_name or athlete.fullname
    changes_text = "، ".join(
        f"{item['label']} از {item['before']} به {item['after']} {item['unit']}"
        for item in alert.changes
    )
    body = f"{athlete_name} | تماس: {athlete.phone} | {changes_text}"[:500]
    payload = json.dumps(
        {
            "title": "تغییر وضعیت شاگرد نیازمند بررسی است",
            "body": body,
            "url": f"/accounts/admin/users/{athlete.pk}/analysis/",
            "tag": f"egofit-health-alert-{alert.pk}",
        },
        ensure_ascii=False,
    )

    sent_count = 0
    subscriptions = MoodReminderSubscription.objects.filter(user=alert.recipient)
    for subscription in subscriptions.iterator(chunk_size=100):
        try:
            webpush(
                subscription_info={
                    "endpoint": subscription.endpoint,
                    "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
                },
                data=payload,
                vapid_private_key=private_key,
                vapid_claims={"sub": getattr(settings, "WEBPUSH_VAPID_SUBJECT", "mailto:push@example.com")},
                ttl=86400,
                timeout=10,
            )
            sent_count += 1
        except WebPushException as exc:
            response = getattr(exc, "response", None)
            if response is not None and response.status_code in {404, 410}:
                subscription.delete()
            logger.warning("Web Push failed for health alert subscription %s: %s", subscription.pk, exc)
        except Exception:
            logger.exception("Unexpected Web Push failure for subscription %s.", subscription.pk)
    return sent_count
