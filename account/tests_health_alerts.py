from __future__ import annotations

import json
from datetime import date, timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from account.models import (
    AthleteHealthAlert,
    CoachHealthAlertPreference,
    DailyHeartRateEntry,
    DailyMoodEntry,
    MoodReminderSubscription,
    User,
)
from account.services.health_alert_service import HealthAlertService
from account.services.mood_service import MoodService
from account.tasks.health_alert_tasks import scan_athlete_health_trends


class HealthAlertServiceTests(TestCase):
    today = date(2026, 11, 12)

    def setUp(self):
        self.admin = self.make_user("Health Alert Admin", "09120011001", is_admin=True)
        self.coach = self.make_user("Health Alert Coach", "09120011002", is_coach=True)
        self.athlete = self.make_user("Health Alert Athlete", "09120011003", coach=self.coach)
        self.other_coach = self.make_user("Other Alert Coach", "09120011004", is_coach=True)
        self.other_athlete = self.make_user(
            "Other Alert Athlete", "09120011005", coach=self.other_coach
        )
        self.service = HealthAlertService()

    @staticmethod
    def make_user(fullname, phone, **kwargs):
        return User.objects.create_user(
            fullname=fullname,
            phone=phone,
            password="test-pass-123",
            **kwargs,
        )

    def add_mood(self, user, entry_date, *, anxiety, motivation, sleep):
        return DailyMoodEntry.objects.create(
            user=user,
            entry_date=entry_date,
            anxiety_score=anxiety,
            motivation_score=motivation,
            sleep_quality_score=sleep,
            training_satisfaction_score=7,
        )

    def add_heart_rate(self, user, entry_date, bpm):
        return DailyHeartRateEntry.objects.create(user=user, entry_date=entry_date, bpm=bpm)

    def seed_worsening_trend(self, user=None, *, today=None):
        user = user or self.athlete
        today = today or self.today
        for days_ago in (50, 40, 35):
            baseline_date = today - timedelta(days=days_ago)
            self.add_mood(user, baseline_date, anxiety=2, motivation=9, sleep=9)
            self.add_heart_rate(user, baseline_date, 60)
        for days_ago in (14, 7, 0):
            current_date = today - timedelta(days=days_ago)
            self.add_mood(user, current_date, anxiety=6, motivation=5, sleep=6)
            self.add_heart_rate(user, current_date, 72)

    @patch("account.services.health_alert_service.timezone.localdate")
    def test_worsening_metrics_create_one_alert_for_coach_and_admin(self, mocked_today):
        mocked_today.return_value = self.today
        self.seed_worsening_trend()

        alerts = self.service.evaluate_user(self.athlete)

        self.assertCountEqual(
            [alert.recipient_id for alert in alerts],
            [self.admin.pk, self.coach.pk],
        )
        for alert in alerts:
            self.assertEqual(alert.athlete, self.athlete)
            self.assertEqual(
                {change["key"] for change in alert.changes},
                {"anxiety", "motivation", "sleep", "heart_rate"},
            )
            heart_rate_change = next(change for change in alert.changes if change["key"] == "heart_rate")
            self.assertEqual((heart_rate_change["before"], heart_rate_change["after"]), (60, 72))
            self.assertEqual(heart_rate_change["sample_counts"], {"baseline": 3, "current": 3})
            self.assertEqual(heart_rate_change["confidence"], "low")
            self.assertTrue(heart_rate_change["follow_up_recommended"])

    @patch("account.services.health_alert_service.timezone.localdate")
    def test_improving_values_and_records_outside_window_do_not_alert(self, mocked_today):
        mocked_today.return_value = self.today
        old_date = self.today - timedelta(days=95)
        self.add_mood(self.athlete, old_date, anxiety=1, motivation=1, sleep=1)
        self.add_heart_rate(self.athlete, old_date, 55)
        self.add_mood(self.athlete, self.today, anxiety=9, motivation=9, sleep=9)
        self.add_heart_rate(self.athlete, self.today, 90)
        for days_ago in (50, 40, 35):
            baseline = self.today - timedelta(days=days_ago)
            self.add_mood(self.other_athlete, baseline, anxiety=8, motivation=3, sleep=4)
            self.add_heart_rate(self.other_athlete, baseline, 90)
        for days_ago in (14, 7, 0):
            current = self.today - timedelta(days=days_ago)
            self.add_mood(self.other_athlete, current, anxiety=2, motivation=9, sleep=9)
            self.add_heart_rate(self.other_athlete, current, 65)

        self.assertEqual(self.service.evaluate_user(self.athlete), [])
        self.assertEqual(self.service.evaluate_user(self.other_athlete), [])
        self.assertFalse(AthleteHealthAlert.objects.exists())

    @patch("account.services.health_alert_service.timezone.localdate")
    def test_mood_submission_hook_evaluates_the_saved_entries(self, mocked_today):
        mocked_today.return_value = self.today
        for days_ago in (50, 40, 35):
            baseline_date = self.today - timedelta(days=days_ago)
            self.add_mood(self.athlete, baseline_date, anxiety=2, motivation=8, sleep=8)
            self.add_heart_rate(self.athlete, baseline_date, 61)
        for days_ago in (14, 7):
            current_date = self.today - timedelta(days=days_ago)
            self.add_mood(self.athlete, current_date, anxiety=5, motivation=6, sleep=7)
            self.add_heart_rate(self.athlete, current_date, 70)

        MoodService().save_entry(
            self.athlete,
            {
                "entry_date": self.today,
                "anxiety_score": 5,
                "motivation_score": 6,
                "sleep_quality_score": 7,
                "training_satisfaction_score": 7,
                "resting_heart_rate_bpm": 70,
            },
        )

        self.assertEqual(
            AthleteHealthAlert.objects.filter(athlete=self.athlete).count(), 2
        )

    @patch("account.services.health_alert_service.timezone.localdate")
    def test_repeated_daily_scan_updates_recent_alert_without_creating_duplicates(self, mocked_today):
        mocked_today.return_value = self.today
        for days_ago in (50, 40, 35):
            baseline_date = self.today - timedelta(days=days_ago)
            self.add_mood(self.athlete, baseline_date, anxiety=2, motivation=8, sleep=8)
            self.add_heart_rate(self.athlete, baseline_date, 60)
        for days_ago in (14, 7, 0):
            current_date = self.today - timedelta(days=days_ago)
            self.add_mood(self.athlete, current_date, anxiety=4, motivation=7, sleep=7)
            self.add_heart_rate(self.athlete, current_date, 65)
        self.service.evaluate_user(self.athlete)

        next_day = self.today + timedelta(days=1)
        mocked_today.return_value = next_day
        self.add_mood(self.athlete, next_day, anxiety=5, motivation=6, sleep=6)
        self.add_heart_rate(self.athlete, next_day, 68)
        self.service.evaluate_user(self.athlete)

        self.assertEqual(AthleteHealthAlert.objects.filter(athlete=self.athlete).count(), 2)
        latest_admin_alert = AthleteHealthAlert.objects.get(
            recipient=self.admin, athlete=self.athlete
        )
        anxiety_change = next(change for change in latest_admin_alert.changes if change["key"] == "anxiety")
        self.assertEqual(anxiety_change["after"], 4)
        self.assertEqual(anxiety_change["sample_counts"]["current"], 4)

    @patch("account.services.health_alert_service.timezone.localdate")
    def test_low_sample_or_stale_current_window_is_not_interpreted_as_a_trend(self, mocked_today):
        mocked_today.return_value = self.today
        for days_ago in (50, 40, 35):
            sample_date = self.today - timedelta(days=days_ago)
            self.add_mood(self.athlete, sample_date, anxiety=2, motivation=8, sleep=8)
            self.add_heart_rate(self.athlete, sample_date, 60)
        # Only two recent samples: below the configured minimum.
        for days_ago in (14, 7):
            sample_date = self.today - timedelta(days=days_ago)
            self.add_mood(self.athlete, sample_date, anxiety=8, motivation=3, sleep=3)
            self.add_heart_rate(self.athlete, sample_date, 80)
        self.assertEqual(self.service.evaluate_user(self.athlete), [])

        # Add enough recent entries, then move the latest entry beyond the freshness window.
        for days_ago in (50, 40, 35):
            sample_date = self.today - timedelta(days=days_ago)
            self.add_mood(self.other_athlete, sample_date, anxiety=2, motivation=8, sleep=8)
            self.add_heart_rate(self.other_athlete, sample_date, 60)
        for days_ago in (20, 15, 10):
            sample_date = self.today - timedelta(days=days_ago)
            self.add_mood(self.other_athlete, sample_date, anxiety=8, motivation=3, sleep=3)
            self.add_heart_rate(self.other_athlete, sample_date, 80)
        self.assertEqual(self.service.evaluate_user(self.other_athlete), [])

    @patch("account.services.health_alert_service.timezone.localdate")
    def test_daily_scan_finds_existing_trends(self, mocked_today):
        mocked_today.return_value = self.today
        self.seed_worsening_trend()

        processed = scan_athlete_health_trends()

        self.assertGreaterEqual(processed, 1)
        self.assertEqual(AthleteHealthAlert.objects.filter(athlete=self.athlete).count(), 2)

    @patch("account.services.health_alert_service.timezone.localdate")
    def test_coach_setting_and_alert_visibility_are_scoped_to_assigned_athletes(self, mocked_today):
        mocked_today.return_value = self.today
        self.seed_worsening_trend(self.athlete)
        self.seed_worsening_trend(self.other_athlete)
        self.service.evaluate_user(self.athlete)
        self.service.evaluate_user(self.other_athlete)
        self.client.force_login(self.coach)

        response = self.client.get(reverse("register:coach_dashboard"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn(self.athlete.phone, body)
        self.assertNotIn(self.other_athlete.phone, body)

        foreign_alert = AthleteHealthAlert.objects.create(
            recipient=self.coach,
            athlete=self.other_athlete,
            observed_date=self.today,
            changes=[{"key": "anxiety", "label": "میزان اضطراب", "before": 2, "after": 5}],
        )
        self.client.post(reverse("register:health_alert_review", args=[foreign_alert.pk]))
        foreign_alert.refresh_from_db()
        self.assertIsNone(foreign_alert.reviewed_at)

        settings_response = self.client.post(
            reverse("register:coach_health_alert_settings"), {"enabled": "false"}
        )
        self.assertRedirects(settings_response, reverse("register:coach_dashboard"))
        preference = CoachHealthAlertPreference.objects.get(user=self.coach)
        self.assertFalse(preference.enabled)

        newly_tracked_athlete = self.make_user(
            "New Alert Athlete", "09120011006", coach=self.coach
        )
        future_scan_date = self.today + timedelta(days=15)
        self.seed_worsening_trend(newly_tracked_athlete, today=future_scan_date)
        mocked_today.return_value = future_scan_date
        new_alerts = self.service.evaluate_user(newly_tracked_athlete)
        self.assertEqual([alert.recipient_id for alert in new_alerts], [self.admin.pk])

    @patch("account.services.health_alert_service.timezone.localdate")
    def test_admin_portal_shows_contact_and_metric_details(self, mocked_today):
        mocked_today.return_value = self.today
        self.seed_worsening_trend()
        self.service.evaluate_user(self.athlete)
        self.client.force_login(self.admin)

        response = self.client.get(reverse("register:admin_search"))

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn(self.athlete.fullname, body)
        self.assertIn(self.athlete.phone, body)
        self.assertIn("ضربان قلب استراحتی", body)
        self.assertIn("60", body)
        self.assertIn("72", body)

    @override_settings(WEBPUSH_VAPID_PUBLIC_KEY="public-key", WEBPUSH_VAPID_PRIVATE_KEY="private-key")
    def test_staff_can_register_browser_push_subscription(self):
        self.client.force_login(self.coach)
        response = self.client.post(
            reverse("register:health_alert_push_api"),
            data=json.dumps(
                {
                    "action": "subscribe",
                    "subscription": {
                        "endpoint": "https://fcm.googleapis.com/fcm/send/test-endpoint",
                        "keys": {"p256dh": "test-public-key", "auth": "test-auth-key"},
                    },
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertTrue(MoodReminderSubscription.objects.filter(user=self.coach).exists())
