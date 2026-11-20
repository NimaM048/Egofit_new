from __future__ import annotations

import calendar
import logging
from datetime import timedelta
from statistics import median

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from account.models import (
    AthleteHealthAlert,
    CoachHealthAlertPreference,
    DailyHeartRateEntry,
    DailyMoodEntry,
    User,
)
from account.selectors.health_alert_selector import HealthAlertSelector


logger = logging.getLogger(__name__)


def _two_months_before(value):
    month_index = value.year * 12 + value.month - 1 - 2
    year, month_zero_based = divmod(month_index, 12)
    month = month_zero_based + 1
    return value.replace(year=year, month=month, day=min(value.day, calendar.monthrange(year, month)[1]))


class HealthAlertService:
    WINDOW_DAYS = 60
    PERIOD_DAYS = 30
    MIN_PERIOD_SAMPLES = 3
    MAX_DATA_AGE_DAYS = 7
    MOOD_METRICS = (
        ("anxiety", "anxiety_score", "میزان اضطراب", "increase", "امتیاز از ۱۰", 2),
        ("sleep", "sleep_quality_score", "کیفیت خواب", "decrease", "امتیاز از ۱۰", 2),
        ("motivation", "motivation_score", "میزان انگیزه", "decrease", "امتیاز از ۱۰", 2),
    )

    @staticmethod
    def is_enabled_for_coach(coach) -> bool:
        preference = CoachHealthAlertPreference.objects.filter(user=coach).only("enabled").first()
        return preference.enabled if preference else True

    def set_coach_enabled(self, coach, enabled: bool):
        preference, _ = CoachHealthAlertPreference.objects.update_or_create(
            user=coach,
            defaults={"enabled": bool(enabled)},
        )
        return preference

    def get_alerts(self, recipient, *, limit: int = 30):
        return HealthAlertSelector.for_recipient(recipient, limit=limit)

    def evaluate_user(self, athlete):
        today = timezone.localdate()
        window_start = today - timedelta(days=self.WINDOW_DAYS - 1)
        mood_entries = list(
            DailyMoodEntry.objects.filter(
                user=athlete,
                entry_date__gte=window_start,
                entry_date__lte=today,
            ).order_by("entry_date", "id")
        )
        heart_entries = list(
            DailyHeartRateEntry.objects.filter(
                user=athlete,
                entry_date__gte=window_start,
                entry_date__lte=today,
            ).order_by("entry_date", "id")
        )

        changes = []
        for key, field, label, direction, unit, threshold in self.MOOD_METRICS:
            change = self._compare(
                mood_entries,
                key,
                field,
                label,
                direction,
                unit,
                threshold=threshold,
                today=today,
            )
            if change:
                changes.append(change)

        heart_change = self._compare(
            heart_entries,
            "heart_rate",
            "bpm",
            "ضربان قلب استراحتی",
            "increase",
            "ضربه در دقیقه",
            threshold=5,
            today=today,
        )
        if heart_change:
            changes.append(heart_change)

        if not changes:
            return []

        recipients = list(User.objects.filter(is_admin=True, is_active=True))
        coach = getattr(athlete, "coach", None)
        if (
            coach
            and coach.is_active
            and coach.is_coach
            and self.is_enabled_for_coach(coach)
            and coach.pk not in {recipient.pk for recipient in recipients}
        ):
            recipients.append(coach)

        alerts = []
        notification_cutoff = timezone.now() - timedelta(days=14)
        with transaction.atomic():
            for recipient in recipients:
                recent_alert = (
                    AthleteHealthAlert.objects.filter(
                        recipient=recipient,
                        athlete=athlete,
                        created_at__gte=notification_cutoff,
                    )
                    .order_by("-created_at", "-id")
                    .first()
                )
                if recent_alert is not None:
                    if recent_alert.changes != changes:
                        recent_alert.changes = changes
                        recent_alert.save(update_fields=["changes"])
                    alerts.append(recent_alert)
                    continue

                alert, created = AthleteHealthAlert.objects.get_or_create(
                    recipient=recipient,
                    athlete=athlete,
                    observed_date=today,
                    defaults={"changes": changes},
                )
                if not created and alert.changes != changes:
                    alert.changes = changes
                    alert.save(update_fields=["changes"])
                alerts.append(alert)
                if created:
                    transaction.on_commit(lambda alert_id=alert.pk: self._schedule_push(alert_id))
        return alerts

    def _schedule_push(self, alert_id: int) -> None:
        if not getattr(settings, "WEBPUSH_VAPID_PRIVATE_KEY", "") or not getattr(
            settings, "WEBPUSH_VAPID_PUBLIC_KEY", ""
        ):
            return
        try:
            from account.tasks.dispatch import dispatch_task
            from account.tasks.health_alert_tasks import send_health_alert_push

            dispatch_task(send_health_alert_push, alert_id)
        except Exception:
            logger.exception("Unable to queue browser push for health alert %s.", alert_id)

    def _compare(self, entries, key, field, label, direction, unit, *, threshold, today):
        baseline_start = today - timedelta(days=self.WINDOW_DAYS - 1)
        current_start = today - timedelta(days=self.PERIOD_DAYS - 1)
        baseline_end = current_start - timedelta(days=1)
        baseline_entries = [entry for entry in entries if baseline_start <= entry.entry_date <= baseline_end]
        current_entries = [entry for entry in entries if current_start <= entry.entry_date <= today]
        if (
            len(baseline_entries) < self.MIN_PERIOD_SAMPLES
            or len(current_entries) < self.MIN_PERIOD_SAMPLES
            or not current_entries
            or current_entries[-1].entry_date < today - timedelta(days=self.MAX_DATA_AGE_DAYS)
        ):
            return None
        before = median(getattr(entry, field) for entry in baseline_entries)
        after = median(getattr(entry, field) for entry in current_entries)
        delta = after - before
        worsened = delta >= threshold if direction == "increase" else delta <= -threshold
        if not worsened:
            return None
        return self._change_payload(
            key,
            label,
            before,
            after,
            baseline_entries[0].entry_date,
            current_entries[-1].entry_date,
            "افزایش" if direction == "increase" else "کاهش",
            unit,
            baseline_count=len(baseline_entries),
            current_count=len(current_entries),
            confidence=self._confidence(min(len(baseline_entries), len(current_entries))),
            baseline_start=baseline_entries[0].entry_date,
            baseline_end=baseline_entries[-1].entry_date,
            current_start=current_entries[0].entry_date,
            current_end=current_entries[-1].entry_date,
        )

    @staticmethod
    def _confidence(sample_count: int) -> str:
        if sample_count >= 10:
            return "high"
        if sample_count >= 5:
            return "moderate"
        return "low"

    @staticmethod
    def _change_payload(
        key,
        label,
        before,
        after,
        before_date,
        after_date,
        direction,
        unit,
        *,
        baseline_count,
        current_count,
        confidence,
        baseline_start,
        baseline_end,
        current_start,
        current_end,
    ):
        return {
            "key": key,
            "label": label,
            "before": before,
            "after": after,
            "delta": after - before,
            "before_date": before_date.isoformat(),
            "after_date": after_date.isoformat(),
            "direction": direction,
            "unit": unit,
            "baseline_period": {"from": baseline_start.isoformat(), "to": baseline_end.isoformat()},
            "current_period": {"from": current_start.isoformat(), "to": current_end.isoformat()},
            "sample_counts": {"baseline": baseline_count, "current": current_count},
            "confidence": confidence,
            "follow_up_recommended": True,
        }
