from __future__ import annotations

import json
from datetime import datetime, timedelta

from django.urls import reverse
from django.utils import timezone

from account.models import (
    AthleteTrainingProfile,
    BodyCircumferenceMeasurement,
    CardioExercise,
    Exercise,
    ExerciseBodyPart,
    ExerciseDifficultyLevel,
    ExerciseEquipmentType,
    ExerciseJointType,
    ExerciseMovementType,
    ExercisePowerType,
    Muscle,
    WorkoutBestRecord,
    WorkoutPerformanceRecord,
    WorkoutProgram,
    WorkoutProgramDay,
    WorkoutProgramExercise,
    WorkoutSession,
    WorkoutSetResult,
    TrainingAdjustmentRecommendation,
)
from api.auth import issue_token_pair
from api.tests_shared import ApiTestCase


class MeasurementAndTrainingApiTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.coach = self.make_user("Test Coach", "09120000303", is_coach=True)
        self.user.coach = self.coach
        self.user.save(update_fields=["coach"])
        muscle, _ = Muscle.objects.get_or_create(name="سینه")
        body_part, _ = ExerciseBodyPart.objects.get_or_create(name="بالاتنه")
        movement, _ = ExerciseMovementType.objects.get_or_create(name="پوش")
        joint, _ = ExerciseJointType.objects.get_or_create(name="چندمفصلی")
        power, _ = ExercisePowerType.objects.get_or_create(name="قدرتی")
        difficulty, _ = ExerciseDifficultyLevel.objects.get_or_create(name="مبتدی")
        self.equipment, _ = ExerciseEquipmentType.objects.get_or_create(name="دمبل")
        self.exercise = Exercise.objects.create(
            name="پرس دمبل",
            primary_muscle=muscle,
            body_part=body_part,
            movement_type=movement,
            joint_type=joint,
            power_type=power,
            difficulty_level=difficulty,
            equipment_type=self.equipment,
        )
        self.program = WorkoutProgram.objects.create(
            user=self.user,
            prescribed_by=self.coach,
            title="برنامه آزمون",
            is_published=True,
        )
        self.day = WorkoutProgramDay.objects.create(program=self.program, name="روز اول", order=1)
        self.item = WorkoutProgramExercise.objects.create(
            day=self.day,
            exercise=self.exercise,
            sets="3",
            reps="8-10",
            rest="90 ثانیه",
        )

    @staticmethod
    def make_user(fullname, phone, **extra):
        from account.models import User

        return User.objects.create_user(fullname=fullname, phone=phone, password="StrongPass123", **extra)

    def test_athlete_measurement_is_timestamped_attributed_and_history_is_owner_scoped(self):
        payload = {
            "weight_kg": "75.5",
            "height_cm": "180",
            "measured_at": "2026-11-10T10:15:00+03:30",
            "measurement_method": "scale",
            "protocol_version": "scale-v2",
            "trial_number": 2,
        }
        created = self.client.post(
            reverse("api:measurements", kwargs={"kind": "circumference"}),
            data=json.dumps(payload),
            content_type="application/json",
            **self.headers,
        )
        self.assertEqual(created.status_code, 201, created.content)
        record = BodyCircumferenceMeasurement.objects.get(user=self.user)
        self.assertEqual(record.measured_at, datetime.fromisoformat(payload["measured_at"]))
        self.assertEqual(record.measurement_source, "self")
        self.assertEqual(record.measured_by_id, self.user.pk)
        self.assertEqual(record.measurement_method, "scale")
        self.user.refresh_from_db()
        self.assertEqual((self.user.weight_kg, self.user.height_cm), (75.5, 180))

        BodyCircumferenceMeasurement.objects.create(user=self.other_user, waist_cm=80)
        history = self.client.get(
            reverse("api:measurements", kwargs={"kind": "circumference"}),
            **self.headers,
        )
        self.assertEqual(history.status_code, 200)
        items = history.json()["data"]["items"]
        self.assertEqual([item["id"] for item in items], [record.pk])
        self.assertEqual(items[0]["trial_number"], 2)

    def test_measurement_api_rejects_unknown_fields_and_future_dates(self):
        endpoint = reverse("api:measurements", kwargs={"kind": "caliper"})
        unknown = self.client.post(
            endpoint,
            data=json.dumps({"triceps_mm": 10, "measurement_source": "admin"}),
            content_type="application/json",
            **self.headers,
        )
        self.assertEqual(unknown.status_code, 400)
        future = self.client.post(
            endpoint,
            data=json.dumps({"triceps_mm": 10, "measured_at": "2099-01-01T12:00:00Z"}),
            content_type="application/json",
            **self.headers,
        )
        self.assertEqual(future.status_code, 400)

    def test_training_preferences_are_structured_and_patch_updates_owner_profile(self):
        response = self.client.patch(
            reverse("api:training_preferences"),
            data=json.dumps({
                "primary_goal": "strength",
                "sessions_per_week": 4,
                "experience_years": 2,
                "session_duration_minutes": 45,
                "preferred_days": ["sat", "mon", "wed"],
                "available_equipment": [self.equipment.pk],
            }),
            content_type="application/json",
            **self.headers,
        )
        self.assertEqual(response.status_code, 200, response.content)
        profile = AthleteTrainingProfile.objects.get(user=self.user)
        self.assertEqual(profile.primary_goal, "strength")
        self.assertEqual(profile.sessions_per_week, 4)
        self.assertEqual(profile.session_duration_minutes, 45)
        self.assertEqual(profile.preferred_days, ["sat", "mon", "wed"])
        self.assertEqual(list(profile.available_equipment.all()), [self.equipment])

        read = self.client.get(reverse("api:training_preferences"), **self.headers)
        self.assertEqual(read.json()["data"]["preferences"]["primary_goal"], "strength")
        self.assertEqual(read.json()["data"]["preferences"]["session_duration_minutes"], 45)

    def test_program_api_exposes_active_deload_sets_and_cycle_phase(self):
        self.program.start_date = timezone.localdate() - timedelta(days=21)
        self.program.generation_metadata = {
            "periodization": {
                "cycle_weeks": 4,
                "deload_week": 4,
                "deload_sets_multiplier": 0.7,
                "description": "حجم ست کمتر",
            }
        }
        self.program.save(update_fields=["start_date", "generation_metadata"])
        self.item.sets = "4"
        self.item.save(update_fields=["sets"])
        self.day.estimated_duration_minutes = 45
        self.day.save(update_fields=["estimated_duration_minutes"])

        response = self.client.get(reverse("api:programs"), **self.headers)

        self.assertEqual(response.status_code, 200, response.content)
        program = response.json()["data"]["items"][0]
        self.assertTrue(program["training_cycle"]["is_deload"])
        self.assertEqual(program["days"][0]["estimated_duration_minutes"], 45)
        movement = program["days"][0]["exercises"][0]
        self.assertEqual(movement["sets"], "2")
        self.assertEqual(movement["prescription"]["sets_min"], 2)
        self.assertEqual(movement["prescription"]["sets_max"], 2)

    def test_session_post_saves_planned_and_actual_set_outcome_and_legacy_records(self):
        payload = {
            "program_id": self.program.pk,
            "program_day_id": self.day.pk,
            "duration_seconds": 2700,
            "difficulty": 7,
            "notes": "فرم مناسب بود",
            "sets": [{
                "program_exercise_id": self.item.pk,
                "slot": "main",
                "set_number": 1,
                "actual_reps": 9,
                "actual_load_kg": "22.5",
                "rpe": 8,
                "rir": 2,
                "completed": True,
            }],
        }
        response = self.client.post(
            reverse("api:workout_sessions"),
            data=json.dumps(payload),
            content_type="application/json",
            **self.headers,
        )
        self.assertEqual(response.status_code, 201, response.content)
        session = WorkoutSession.objects.get(user=self.user)
        result = WorkoutSetResult.objects.get(session=session)
        self.assertEqual((session.program_id, session.program_day_id, session.difficulty), (self.program.pk, self.day.pk, 7))
        self.assertEqual((result.planned_reps, result.actual_reps, result.actual_load_kg), ("8-10", 9, 22.5))
        self.assertEqual((result.planned_sets_min, result.planned_sets_max), (3, 3))
        self.assertEqual((result.planned_reps_min, result.planned_reps_max), (8, 10))
        self.assertTrue(WorkoutPerformanceRecord.objects.filter(user=self.user, exercise=self.exercise, value=22.5).exists())
        self.assertTrue(WorkoutBestRecord.objects.filter(user=self.user, exercise=self.exercise, value=22.5).exists())

        history = self.client.get(reverse("api:workout_sessions"), **self.headers)
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.json()["data"]["items"][0]["sets"][0]["actual_reps"], 9)

    def test_session_post_is_idempotent_and_rejects_key_reuse_with_different_payload(self):
        endpoint = reverse("api:workout_sessions")
        payload = {
            "program_id": self.program.pk,
            "program_day_id": self.day.pk,
            "sets": [{
                "program_exercise_id": self.item.pk,
                "set_number": 1,
                "actual_reps": 9,
                "actual_load_kg": "22.5",
            }],
        }
        first = self.client.post(
            endpoint,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="workout-submit-1",
            **self.headers,
        )
        retry = self.client.post(
            endpoint,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="workout-submit-1",
            **self.headers,
        )
        changed = {**payload, "notes": "متن متفاوت"}
        conflict = self.client.post(
            endpoint,
            data=json.dumps(changed),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="workout-submit-1",
            **self.headers,
        )

        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(retry.status_code, 200, retry.content)
        self.assertEqual(conflict.status_code, 400)
        self.assertEqual(WorkoutSession.objects.filter(user=self.user).count(), 1)
        self.assertEqual(WorkoutSetResult.objects.filter(session__user=self.user).count(), 1)

    def test_progression_suggestion_uses_repeated_success_and_requires_coach_review(self):
        endpoint = reverse("api:workout_sessions")
        payload = {
            "program_id": self.program.pk,
            "program_day_id": self.day.pk,
            "sets": [{
                "program_exercise_id": self.item.pk,
                "set_number": 1,
                "actual_reps": 10,
                "actual_load_kg": "22.5",
                "rpe": 7,
                "rir": 3,
            }],
        }
        for key in ("progression-exposure-1", "progression-exposure-2"):
            response = self.client.post(
                endpoint,
                data=json.dumps(payload),
                content_type="application/json",
                HTTP_IDEMPOTENCY_KEY=key,
                **self.headers,
            )
            self.assertEqual(response.status_code, 201, response.content)

        second_session = WorkoutSession.objects.filter(user=self.user).order_by("-started_at", "-pk").first()
        recommendation = TrainingAdjustmentRecommendation.objects.get(source_session=second_session)
        self.assertEqual(recommendation.recommendation_type, TrainingAdjustmentRecommendation.RecommendationType.INCREASE_LOAD)
        self.assertEqual(recommendation.suggested_load_kg, 23)
        self.assertEqual(recommendation.confidence, TrainingAdjustmentRecommendation.Confidence.MEDIUM)
        self.assertEqual(recommendation.evidence["exposure_count"], 2)

        coach_headers = {"HTTP_AUTHORIZATION": f"Bearer {issue_token_pair(user=self.coach)['access_token']}"}
        review = self.client.patch(
            reverse(
                "api:coach_review_training_recommendation",
                kwargs={"athlete_id": self.user.pk, "recommendation_id": recommendation.pk},
            ),
            data=json.dumps({"status": "accepted", "coach_note": "بازبینی شد"}),
            content_type="application/json",
            **coach_headers,
        )

        self.assertEqual(review.status_code, 200, review.content)
        self.assertFalse(review.json()["data"]["applied_to_program"])
        recommendation.refresh_from_db()
        self.assertEqual(recommendation.status, TrainingAdjustmentRecommendation.Status.ACCEPTED)
        self.assertEqual(recommendation.reviewed_load_kg, 23)
        self.item.refresh_from_db()
        self.assertIsNone(self.item.target_load_kg)

    def test_cardio_session_snapshots_interval_targets_and_records_actual_heart_rate(self):
        cardio = CardioExercise.objects.create(name="دوچرخه تناوبی", performance_type="interval")
        cardio_item = WorkoutProgramExercise.objects.create(
            day=self.day,
            cardio_exercise=cardio,
            sets="1",
            reps="",
            cardio_duration_seconds=900,
            cardio_interval_count=6,
            cardio_work_seconds=60,
            cardio_recovery_seconds=90,
            target_rpe=6,
        )
        response = self.client.post(
            reverse("api:workout_sessions"),
            data=json.dumps({
                "program_id": self.program.pk,
                "program_day_id": self.day.pk,
                "sets": [{
                    "program_exercise_id": cardio_item.pk,
                    "set_number": 1,
                    "duration_seconds": 900,
                    "average_heart_rate_bpm": 142,
                    "peak_heart_rate_bpm": 161,
                    "rpe": 6,
                }],
            }),
            content_type="application/json",
            **self.headers,
        )

        self.assertEqual(response.status_code, 201, response.content)
        result = WorkoutSetResult.objects.get(cardio_exercise=cardio)
        self.assertEqual(
            (result.planned_duration_seconds, result.planned_interval_count, result.planned_work_seconds, result.planned_recovery_seconds),
            (900, 6, 60, 90),
        )
        self.assertEqual((result.duration_seconds, result.average_heart_rate_bpm, result.peak_heart_rate_bpm), (900, 142, 161))

    def test_session_endpoint_cannot_write_foreign_program_or_an_exercise_from_another_day(self):
        other_program = WorkoutProgram.objects.create(user=self.other_user, title="برنامه دیگر", is_published=True)
        other_day = WorkoutProgramDay.objects.create(program=other_program, name="روز", order=1)
        response = self.client.post(
            reverse("api:workout_sessions"),
            data=json.dumps({
                "program_id": other_program.pk,
                "program_day_id": other_day.pk,
                "sets": [{"program_exercise_id": self.item.pk, "set_number": 1, "actual_reps": 5}],
            }),
            content_type="application/json",
            **self.headers,
        )
        self.assertEqual(response.status_code, 404)

    def test_workout_portal_renders_and_records_actual_set_outcomes(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("register:profile_workout_programs"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "نتیجه واقعی هر ست")

        prefix = f"session_{self.item.pk}_main_1"
        response = self.client.post(
            reverse("register:profile_workout_program_performance", args=[self.program.pk]),
            {
                "day_id": self.day.pk,
                "difficulty": 6,
                "session_duration_minutes": 45,
                "session_notes": "ثبت از رابط کاربری",
                f"{prefix}_attempted": "1",
                f"{prefix}_completed": "1",
                f"{prefix}_actual_reps": "9",
                f"{prefix}_actual_load_kg": "22.5",
                f"{prefix}_rpe": "8",
            },
        )
        self.assertEqual(response.status_code, 302)
        session = WorkoutSession.objects.get(user=self.user, program=self.program, program_day=self.day)
        result = WorkoutSetResult.objects.get(session=session)
        self.assertEqual((result.actual_reps, result.actual_load_kg, result.planned_reps), (9, 22.5, "8-10"))

    def test_coach_analysis_hides_non_training_health_details(self):
        from account.models import UserHealthRecord

        UserHealthRecord.objects.create(
            user=self.user,
            category=UserHealthRecord.CategoryChoices.INJURY,
            title="آسیب زانو",
            details="جزئیات بالینی محرمانه",
            body_region=UserHealthRecord.BodyRegion.LEG,
            severity=5,
            training_restriction=True,
            training_restriction_notes="اسکوات سنگین انجام نشود",
        )
        UserHealthRecord.objects.create(
            user=self.user,
            category=UserHealthRecord.CategoryChoices.MEDICATION,
            title="داروی محرمانه",
            details="داده محرمانه",
        )
        coach_headers = {"HTTP_AUTHORIZATION": f"Bearer {issue_token_pair(user=self.coach)['access_token']}"}
        response = self.client.get(
            reverse("api:coach_athlete_analysis", kwargs={"athlete_id": self.user.pk}),
            **coach_headers,
        )
        self.assertEqual(response.status_code, 200)
        health_records = response.json()["data"]["health_records"]
        self.assertEqual(len(health_records), 1)
        self.assertNotIn("details", health_records[0])
        self.assertNotIn("title", health_records[0])

    def test_workout_api_stores_warmups_but_excludes_them_from_strength_metrics(self):
        from account.models import ExerciseStrengthEstimate

        self.item.warmup_sets = [{"percent": 50, "load_kg": "25", "reps": 5}]
        self.item.save(update_fields=["warmup_sets"])
        response = self.client.post(
            reverse("api:workout_sessions"),
            data=json.dumps({
                "program_id": self.program.pk,
                "program_day_id": self.day.pk,
                "sets": [
                    {
                        "program_exercise_id": self.item.pk,
                        "slot": "main",
                        "set_number": 1,
                        "is_warmup": True,
                        "actual_reps": 5,
                        "actual_load_kg": "100",
                    },
                    {
                        "program_exercise_id": self.item.pk,
                        "slot": "main",
                        "set_number": 1,
                        "is_warmup": False,
                        "actual_reps": 8,
                        "actual_load_kg": "50",
                        "rir": 2,
                    },
                ],
            }),
            content_type="application/json",
            **self.headers,
        )
        self.assertEqual(response.status_code, 201, response.content)
        serialized_sets = response.json()["data"]["session"]["sets"]
        self.assertEqual([row["is_warmup"] for row in serialized_sets], [True, False])
        self.assertEqual(ExerciseStrengthEstimate.objects.count(), 1)
        self.assertFalse(ExerciseStrengthEstimate.objects.filter(source_set__is_warmup=True).exists())
        self.assertTrue(WorkoutPerformanceRecord.objects.filter(user=self.user, value=50).exists())
        self.assertFalse(WorkoutPerformanceRecord.objects.filter(user=self.user, value=100).exists())

        insights = self.client.get(reverse("api:training_insights"), **self.headers)
        self.assertEqual(insights.status_code, 200, insights.content)
        self.assertEqual(len(insights.json()["data"]["estimated_1rm_history"]), 1)

    def test_athlete_exercise_preferences_are_owner_scoped_and_validated(self):
        endpoint = reverse("api:exercise_preferences")
        saved = self.client.put(
            endpoint,
            data=json.dumps({
                "exercise_id": self.exercise.pk,
                "status": "pain",
                "reason": "درد مچ دست",
            }),
            content_type="application/json",
            **self.headers,
        )
        self.assertEqual(saved.status_code, 200, saved.content)
        self.assertEqual(saved.json()["data"]["preference"]["status"], "pain")
        listed = self.client.get(endpoint, **self.headers)
        self.assertEqual([row["exercise_id"] for row in listed.json()["data"]["items"]], [self.exercise.pk])

        invalid = self.client.put(
            endpoint,
            data=json.dumps({"exercise_id": self.exercise.pk, "status": "disable"}),
            content_type="application/json",
            **self.headers,
        )
        self.assertEqual(invalid.status_code, 400)
        deleted = self.client.delete(
            endpoint,
            data=json.dumps({"exercise_id": self.exercise.pk}),
            content_type="application/json",
            **self.headers,
        )
        self.assertEqual(deleted.status_code, 200, deleted.content)
        self.assertFalse(self.user.exercise_preferences.exists())
