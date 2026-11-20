from __future__ import annotations

import jdatetime
from django.db import transaction
from django.utils import timezone

from account.models import DailyHeartRateEntry, DailyMoodEntry, MeasurementSource, User


class MoodService:
    SERIES = (
        ("anxiety", "anxiety_score", "میزان اضطراب"),
        ("motivation", "motivation_score", "میزان انگیزه"),
        ("sleep", "sleep_quality_score", "کیفیت خواب"),
        ("training", "training_satisfaction_score", "رضایت از کیفیت تمرین"),
        ("readiness", "training_readiness_score", "آمادگی تمرین"),
        ("fatigue", "fatigue_score", "خستگی"),
        ("soreness", "muscle_soreness_score", "کوفتگی عضلانی"),
    )

    def get_entries(self, user: User):
        return list(DailyMoodEntry.objects.filter(user=user).order_by("-entry_date", "-id"))

    def get_entry(self, user: User, entry_date=None):
        entry_date = entry_date or timezone.localdate()
        return DailyMoodEntry.objects.filter(user=user, entry_date=entry_date).first()

    def get_heart_rate_entries(self, user: User):
        return list(DailyHeartRateEntry.objects.filter(user=user).order_by("-entry_date", "-id"))

    def get_heart_rate_entry(self, user: User, entry_date=None):
        entry_date = entry_date or timezone.localdate()
        return DailyHeartRateEntry.objects.filter(user=user, entry_date=entry_date).first()

    def get_entry_bundle(self, user: User, entry_date=None):
        entry_date = entry_date or timezone.localdate()
        return self.get_entry(user, entry_date), self.get_heart_rate_entry(user, entry_date)

    def get_form_initial(self, entry=None, heart_rate_entry=None):
        initial = {"entry_date": entry.entry_date if entry else timezone.localdate()}
        if entry:
            for _, field, _ in self.SERIES:
                initial[field] = getattr(entry, field)
        if heart_rate_entry:
            initial["resting_heart_rate_bpm"] = heart_rate_entry.bpm
            if heart_rate_entry.measured_at:
                initial["heart_rate_measured_at"] = timezone.localtime(heart_rate_entry.measured_at).replace(second=0, microsecond=0)
        return initial

    def save_entry(self, user: User, cleaned_data: dict) -> DailyMoodEntry:
        entry_date = cleaned_data["entry_date"]
        mood_values = {
            field: cleaned_data.get(field)
            for _, field, _ in self.SERIES
        }
        with transaction.atomic():
            entry, _ = DailyMoodEntry.objects.update_or_create(user=user, entry_date=entry_date, defaults=mood_values)
            DailyHeartRateEntry.objects.update_or_create(
                user=user,
                entry_date=entry_date,
                defaults={
                    "bpm": cleaned_data["resting_heart_rate_bpm"],
                    "measured_at": cleaned_data.get("heart_rate_measured_at"),
                    "source": MeasurementSource.SELF_REPORTED,
                    "measurement_context": DailyHeartRateEntry.MeasurementContext.RESTING_MORNING,
                },
            )
            from account.services.health_alert_service import HealthAlertService

            HealthAlertService().evaluate_user(user)
        return entry

    def get_chart_data(self, user: User) -> dict:
        mood_entries = list(reversed(self.get_entries(user)))
        heart_rate_entries = list(reversed(self.get_heart_rate_entries(user)))

        def jalali(value):
            return jdatetime.date.fromgregorian(date=value).strftime("%Y/%m/%d")

        return {
            "mood": {
                "labels": [jalali(entry.entry_date) for entry in mood_entries],
                "series": {key: [getattr(entry, field) for entry in mood_entries] for key, field, _ in self.SERIES},
            },
            "heart_rate": {
                "labels": [jalali(entry.entry_date) for entry in heart_rate_entries],
                "data": [entry.bpm for entry in heart_rate_entries],
            },
        }

    def get_dashboard_context(self, user: User) -> dict:
        entries = self.get_entries(user)
        heart_rate_entries = self.get_heart_rate_entries(user)
        latest = entries[0] if entries else None
        return {
            "mood_entries": entries,
            "heart_rate_entries": heart_rate_entries,
            "latest_mood_entry": latest,
            "mood_chart_data": self.get_chart_data(user),
        }
