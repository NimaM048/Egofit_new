from __future__ import annotations

import json
import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from account.models import (
    DailyHeartRateEntry,
    DailyMoodEntry,
    MoodReminderPreference,
    MoodReminderSubscription,
)
from sport_shop.task_queue import shared_task


logger = logging.getLogger(__name__)


@shared_task(name="account.send_due_mood_reminders")
def send_due_mood_reminders() -> int:
    """Send each due mood reminder once per selected local calendar day."""
    private_key = str(getattr(settings, "WEBPUSH_VAPID_PRIVATE_KEY", "") or "").replace("\\n", "\n").strip()
    if not private_key:
        logger.debug("Mood reminders are not sent because the VAPID private key is not configured.")
        return 0

    sent_count = 0
    preference_ids = MoodReminderPreference.objects.filter(enabled=True).values_list("id", flat=True)
    for preference_id in preference_ids.iterator(chunk_size=200):
        try:
            sent_count += _send_if_due(preference_id, private_key)
        except Exception:
            logger.exception("Failed to process mood reminder preference %s.", preference_id)
    return sent_count


def _send_if_due(preference_id: int, private_key: str) -> int:
    from pywebpush import WebPushException, webpush

    with transaction.atomic():
        preference = (
            MoodReminderPreference.objects.select_for_update()
            .select_related("user")
            .filter(pk=preference_id, enabled=True)
            .first()
        )
        if preference is None:
            return 0

        try:
            zone = ZoneInfo(preference.time_zone)
        except (ZoneInfoNotFoundError, ValueError):
            logger.warning("Invalid timezone on mood reminder preference %s.", preference.pk)
            return 0

        now_local = timezone.now().astimezone(zone)
        local_weekday = (now_local.weekday() + 2) % 7
        if (
            local_weekday not in preference.weekdays
            or now_local.time().replace(tzinfo=None) < preference.remind_at
            or preference.last_sent_local_date == now_local.date()
        ):
            return 0

        subscriptions = list(MoodReminderSubscription.objects.filter(user=preference.user))
        if not subscriptions:
            return 0

        mood_recorded = DailyMoodEntry.objects.filter(
            user=preference.user, entry_date=now_local.date()
        ).exists()
        heart_rate_recorded = DailyHeartRateEntry.objects.filter(
            user=preference.user, entry_date=now_local.date()
        ).exists()
        if mood_recorded and heart_rate_recorded:
            preference.last_sent_local_date = now_local.date()
            preference.save(update_fields=["last_sent_local_date", "updated_at"])
            return 0

        if not mood_recorded and not heart_rate_recorded:
            body = "روحیه و ضربان قلب استراحتی امروزتان را ثبت کنید."
        elif not mood_recorded:
            body = "روحیه امروزتان را ثبت کنید."
        else:
            body = "ضربان قلب استراحتی امروزتان را ثبت کنید."

        payload = json.dumps(
            {
                "title": "زمان ثبت وضعیت روزانه است",
                "body": body,
                "url": "/accounts/profile/mood/",
            },
            ensure_ascii=False,
        )
        delivered = False
        for subscription in subscriptions:
            try:
                webpush(
                    subscription_info={
                        "endpoint": subscription.endpoint,
                        "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
                    },
                    data=payload,
                    vapid_private_key=private_key,
                    vapid_claims={
                        "sub": getattr(settings, "WEBPUSH_VAPID_SUBJECT", "mailto:push@example.com")
                    },
                    ttl=86400,
                    timeout=10,
                )
                delivered = True
            except WebPushException as exc:
                response = getattr(exc, "response", None)
                if response is not None and response.status_code in {404, 410}:
                    subscription.delete()
                logger.warning("Web Push failed for subscription %s: %s", subscription.pk, exc)
            except Exception:
                logger.exception("Unexpected Web Push failure for subscription %s.", subscription.pk)

        if delivered:
            preference.last_sent_local_date = now_local.date()
            preference.save(update_fields=["last_sent_local_date", "updated_at"])
            return 1
    return 0
