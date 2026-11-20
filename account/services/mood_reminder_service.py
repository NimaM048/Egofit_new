from __future__ import annotations

import hashlib
import ipaddress
from datetime import time, timedelta
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from account.models import MoodReminderPreference, MoodReminderSubscription


DEFAULT_WEEKDAYS = [0, 2, 4]  # Saturday, Monday, Wednesday
ALLOWED_PUSH_ENDPOINTS = {
    "fcm.googleapis.com",
    "web.push.apple.com",
    "push.services.mozilla.com",
}
ALLOWED_PUSH_ENDPOINT_SUFFIXES = (
    ".push.services.mozilla.com",
    ".notify.windows.com",
    ".push.services.opera.com",
)


class MoodReminderService:
    """Manage mood reminder schedules and browser push subscriptions."""

    def get_page_context(self, user) -> dict:
        preference = MoodReminderPreference.objects.filter(user=user).first()
        now = timezone.now()
        enabled = bool(preference and preference.enabled)
        deferred_until = preference.prompt_deferred_until if preference else None
        prompt_due = not enabled and (deferred_until is None or deferred_until <= now)
        selected_weekdays = preference.weekdays if preference else DEFAULT_WEEKDAYS
        weekday_labels = ["شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه"]
        weekday_options = [
            {"value": value, "label": label, "selected": value in selected_weekdays}
            for value, label in enumerate(weekday_labels)
        ]
        return {
            "mood_reminder": preference,
            "mood_reminder_enabled": enabled,
            "mood_reminder_weekdays": selected_weekdays,
            "mood_reminder_weekday_options": weekday_options,
            "mood_reminder_selected_weekday_labels": [
                option["label"] for option in weekday_options if option["selected"]
            ],
            "mood_reminder_time": preference.remind_at.strftime("%H:%M") if preference else "20:00",
            "mood_reminder_prompt_due": prompt_due,
            "mood_reminder_vapid_public_key": getattr(settings, "WEBPUSH_VAPID_PUBLIC_KEY", ""),
        }

    def save_subscription(self, user, subscription: dict) -> MoodReminderSubscription:
        endpoint = str(subscription.get("endpoint") or "").strip()
        keys = subscription.get("keys")
        if not endpoint or len(endpoint) > 4096 or not isinstance(keys, dict):
            raise ValueError("اشتراک اعلان مرورگر معتبر نیست.")

        parsed = urlsplit(endpoint)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("نشانی اشتراک مرورگر معتبر نیست.")
        host = parsed.hostname.lower().rstrip(".")
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("نشانی اشتراک مرورگر معتبر نیست.") from exc
        trusted_host = host in ALLOWED_PUSH_ENDPOINTS or any(
            host.endswith(suffix) for suffix in ALLOWED_PUSH_ENDPOINT_SUFFIXES
        )
        if not trusted_host or port not in (None, 443):
            raise ValueError("درگاه اعلان این مرورگر پشتیبانی نمی‌شود.")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if host == "localhost" or (address and not address.is_global):
            raise ValueError("نشانی اشتراک مرورگر معتبر نیست.")

        p256dh = str(keys.get("p256dh") or "").strip()
        auth = str(keys.get("auth") or "").strip()
        if not p256dh or len(p256dh) > 256 or not auth or len(auth) > 128:
            raise ValueError("کلیدهای اشتراک اعلان مرورگر ناقص هستند.")

        endpoint_hash = hashlib.sha256(endpoint.encode("utf-8")).hexdigest()
        obj, _ = MoodReminderSubscription.objects.update_or_create(
            endpoint_hash=endpoint_hash,
            defaults={"user": user, "endpoint": endpoint, "p256dh": p256dh, "auth": auth},
        )
        return obj

    def save_settings(self, user, *, remind_at: str, weekdays: list, time_zone: str):
        try:
            parsed_time = time.fromisoformat(str(remind_at))
        except (TypeError, ValueError) as exc:
            raise ValueError("ساعت یادآوری معتبر نیست.") from exc
        if parsed_time.tzinfo is not None or parsed_time.second or parsed_time.microsecond:
            raise ValueError("ساعت یادآوری معتبر نیست.")

        if not isinstance(weekdays, list) or not weekdays:
            raise ValueError("حداقل یک روز برای یادآوری انتخاب کنید.")
        try:
            selected_days = sorted({int(day) for day in weekdays})
        except (TypeError, ValueError) as exc:
            raise ValueError("روزهای انتخاب‌شده معتبر نیستند.") from exc
        if any(day < 0 or day > 6 for day in selected_days):
            raise ValueError("روزهای انتخاب‌شده معتبر نیستند.")

        time_zone = str(time_zone or "").strip()
        if len(time_zone) > 64:
            raise ValueError("منطقه زمانی معتبر نیست.")
        try:
            zone = ZoneInfo(time_zone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("منطقه زمانی این دستگاه پشتیبانی نمی‌شود.") from exc

        if not MoodReminderSubscription.objects.filter(user=user).exists():
            raise ValueError("ابتدا اجازه اعلان مرورگر را فعال کنید.")
        if not getattr(settings, "WEBPUSH_VAPID_PRIVATE_KEY", "") or not getattr(
            settings, "WEBPUSH_VAPID_PUBLIC_KEY", ""
        ):
            raise ValueError("ارسال اعلان هنوز در تنظیمات سرور فعال نشده است.")

        now_local = timezone.now().astimezone(zone)
        local_weekday = (now_local.weekday() + 2) % 7
        with transaction.atomic():
            preference, _ = MoodReminderPreference.objects.select_for_update().get_or_create(user=user)
            preference.weekdays = selected_days
            preference.remind_at = parsed_time
            preference.time_zone = time_zone
            preference.enabled = True
            preference.prompt_deferred_until = None
            if (
                local_weekday in selected_days
                and now_local.time().replace(tzinfo=None) >= parsed_time
                and preference.last_sent_local_date != now_local.date()
            ):
                # Activation after today's chosen time starts with the next selected day.
                preference.last_sent_local_date = now_local.date()
            preference.save(
                update_fields=[
                    "weekdays",
                    "remind_at",
                    "time_zone",
                    "enabled",
                    "prompt_deferred_until",
                    "last_sent_local_date",
                    "updated_at",
                ]
            )
        return preference

    def defer_prompt(self, user):
        with transaction.atomic():
            preference, _ = MoodReminderPreference.objects.select_for_update().get_or_create(user=user)
            preference.prompt_deferred_until = timezone.now() + timedelta(days=10)
            preference.save(update_fields=["prompt_deferred_until", "updated_at"])
        return preference.prompt_deferred_until

    def disable(self, user):
        with transaction.atomic():
            preference = MoodReminderPreference.objects.select_for_update().filter(user=user).first()
            if preference is None:
                return None
            preference.enabled = False
            preference.prompt_deferred_until = timezone.now() + timedelta(days=10)
            preference.save(update_fields=["enabled", "prompt_deferred_until", "updated_at"])
        return preference
