import json
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from account.admin_forms import AutomaticProgrammingForm
from account.services.automatic_program_service import AutomaticProgramService
from account.services.profile_service import ProfileService
from account.models import (
    AthleteTrainingProfile,
    CorrectiveExercise,
    DailyMoodEntry,
    Exercise,
    ExerciseAbnormalityType,
    ExerciseBodyPart,
    ExerciseDifficultyLevel,
    ExerciseEquipmentType,
    ExerciseGoal,
    ExerciseJointType,
    ExerciseMovementType,
    ExercisePowerType,
    ExerciseRepetitionType,
    ExerciseRestType,
    ExerciseSecondaryMovementType,
    ExerciseSetType,
    ExerciseStrengthEstimate,
    Muscle,
    User,
    WorkoutBestRecord,
    WorkoutProgram,
    WorkoutProgramCorrective,
    WorkoutProgramDay,
    WorkoutProgramDayProgress,
    WorkoutProgramExercise,
    WorkoutPerformanceRecord,
    WorkoutProgramFeedback,
    Notification,
    TrainingAdjustmentRecommendation,
    WorkoutSession,
    WorkoutSetResult,
)
from account.services.training_data_service import TrainingDataService
from account.services.training_periodization_service import TrainingPeriodizationService
from account.services.training_recommendation_review_service import TrainingRecommendationReviewService


class GymProgramPortalTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create(
            phone="09129990001",
            fullname="gym_admin",
            is_admin=True,
        )
        self.client_user = User.objects.create(
            phone="09129990002",
            fullname="gym_client",
        )
        muscle, _ = Muscle.objects.get_or_create(name="سینه")
        body_part, _ = ExerciseBodyPart.objects.get_or_create(name="بالاتنه")
        movement_type, _ = ExerciseMovementType.objects.get_or_create(name="پوش")
        joint_type, _ = ExerciseJointType.objects.get_or_create(name="چند مفصلی")
        power_type, _ = ExercisePowerType.objects.get_or_create(name="قدرتی")
        difficulty, _ = ExerciseDifficultyLevel.objects.get_or_create(name="مبتدی")
        equipment, _ = ExerciseEquipmentType.objects.get_or_create(name="هالتر")
        self.exercise_one = Exercise.objects.create(
            name="پرس سینه هالتر",
            primary_muscle=muscle,
            body_part=body_part,
            movement_type=movement_type,
            joint_type=joint_type,
            power_type=power_type,
            difficulty_level=difficulty,
            equipment_type=equipment,
        )
        self.exercise_two = Exercise.objects.create(
            name="پرس سینه دمبل",
            primary_muscle=muscle,
            body_part=body_part,
            movement_type=movement_type,
            joint_type=joint_type,
            power_type=power_type,
            difficulty_level=difficulty,
            equipment_type=equipment,
        )
        self.exercise_three = Exercise.objects.create(
            name="قفسه سینه دستگاه",
            primary_muscle=muscle,
            body_part=body_part,
            movement_type=movement_type,
            joint_type=joint_type,
            power_type=power_type,
            difficulty_level=difficulty,
            equipment_type=equipment,
        )
        ExerciseSetType.objects.get_or_create(set_count="۳", goal="")
        ExerciseRepetitionType.objects.get_or_create(reps="۸ تا ۱۲", goal="")
        ExerciseRestType.objects.get_or_create(rest_time="۶۰ ثانیه", goal="")
        abnormality, _ = ExerciseAbnormalityType.objects.get_or_create(name="گرد پشتی")
        self.corrective = CorrectiveExercise.objects.create(
            name="کشش سینه",
            equipment=equipment,
            abnormality_type=abnormality,
        )

    def _make_training_recommendation(self, *, user=None, exercise=None, evidence=None, suggested_load="45"):
        user = user or self.client_user
        exercise = exercise or self.exercise_one
        program = WorkoutProgram.objects.create(
            user=user,
            prescribed_by=self.admin,
            title=f"برنامهٔ سابقه {WorkoutSession.objects.count() + 1}",
        )
        session = WorkoutSession.objects.create(user=user, program=program)
        return TrainingAdjustmentRecommendation.objects.create(
            user=user,
            program=program,
            source_session=session,
            exercise=exercise,
            movement_name=exercise.name,
            recommendation_type=TrainingAdjustmentRecommendation.RecommendationType.INCREASE_LOAD,
            confidence=TrainingAdjustmentRecommendation.Confidence.HIGH,
            baseline_load_kg=Decimal("40"),
            suggested_load_kg=Decimal(suggested_load) if suggested_load is not None else None,
            rationale="تکرار هدف با RIR مناسب تکمیل شد.",
            evidence=evidence or {"exposure_count": 1, "session_ids": [session.pk], "outcomes": []},
        )

    def test_recommendation_queue_groups_pending_rows_and_shows_set_evidence(self):
        older = self._make_training_recommendation(
            evidence={"exposure_count": 1, "outcomes": [{"session_id": 1, "average_reps": 10, "average_rpe": 8, "average_rir": 2}]}
        )
        latest = self._make_training_recommendation(
            evidence={
                "exposure_count": 2,
                "session_ids": [older.source_session_id],
                "outcomes": [{"session_id": older.source_session_id, "average_reps": 12, "average_rpe": 7, "average_rir": 3}],
            },
            suggested_load="47.5",
        )
        WorkoutSetResult.objects.create(
            session=older.source_session,
            exercise=self.exercise_one,
            slot=WorkoutSetResult.Slot.MAIN,
            set_number=1,
            planned_load_kg=Decimal("40"),
            actual_load_kg=Decimal("42.5"),
            actual_reps=12,
            rpe=7,
            rir=3,
        )
        self.client.force_login(self.admin)

        response = self.client.get(reverse("register:admin_training_recommendations"), {"tab": "pending"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["recommendation_page"].paginator.count, 1)
        self.assertEqual(response.context["selected_recommendation"].pk, latest.pk)
        self.assertEqual(
            [item.pk for item in response.context["selected_recommendation_group"]],
            [latest.pk, older.pk],
        )
        self.assertContains(response, "47.50 kg")
        self.assertContains(response, "42.5")
        self.assertContains(response, "RPE 7")

    def test_profile_links_to_compact_queue_instead_of_rendering_recommendation_cards(self):
        self._make_training_recommendation()
        self.client.force_login(self.admin)

        response = self.client.get(reverse("register:admin_user_hub", args=[self.client_user.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["training_recommendation_pending_count"], 1)
        self.assertContains(response, "بازبینی پیشنهادها")
        self.assertNotContains(response, "training_recommendation_queue_review")

    def test_group_review_supersedes_older_rows_and_opens_next_pending_group(self):
        older = self._make_training_recommendation()
        latest = self._make_training_recommendation(suggested_load="47.5")
        next_item = self._make_training_recommendation(exercise=self.exercise_two)
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("register:training_recommendation_queue_review", args=[latest.pk]),
            {"status": "edited", "reviewed_load_kg": "46", "coach_note": "بار محافظه‌کارانه‌تر", "q": "", "user": ""},
        )

        latest.refresh_from_db()
        older.refresh_from_db()
        self.assertEqual(latest.status, TrainingAdjustmentRecommendation.Status.EDITED)
        self.assertEqual(latest.reviewed_load_kg, Decimal("46"))
        self.assertEqual(latest.reviewed_by, self.admin)
        self.assertIsNotNone(latest.reviewed_at)
        self.assertEqual(older.status, TrainingAdjustmentRecommendation.Status.SUPERSEDED)
        self.assertEqual(older.superseded_by_id, latest.pk)
        self.assertEqual(older.reviewed_by, self.admin)
        self.assertIn(f"selected={next_item.pk}", response.url)

        archived = self.client.get(
            reverse("register:admin_training_recommendations"),
            {"tab": "merged", "selected": older.pk},
        )
        self.assertContains(archived, "با پیشنهاد جدیدتر ادغام شد")

    def test_coach_queue_is_limited_to_assigned_athletes_and_reviews_reject_other_rows(self):
        coach = User.objects.create(phone="09129990011", fullname="gym_coach", is_coach=True)
        self.client_user.coach = coach
        self.client_user.save(update_fields=["coach"])
        own_recommendation = self._make_training_recommendation()
        other_user = User.objects.create(phone="09129990012", fullname="other_client")
        other_recommendation = self._make_training_recommendation(user=other_user, exercise=self.exercise_two)
        self.client.force_login(coach)

        response = self.client.get(reverse("register:admin_training_recommendations"), {"tab": "pending"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["recommendation_page"].paginator.count, 1)
        self.assertEqual(response.context["selected_recommendation"].pk, own_recommendation.pk)
        self.assertEqual(
            self.client.post(
                reverse("register:training_recommendation_queue_review", args=[other_recommendation.pk]),
                {"status": "rejected"},
            ).status_code,
            404,
        )

    def test_approved_progression_is_linked_to_one_generated_program_only(self):
        section = ExerciseSecondaryMovementType.objects.create(name="هدف مصرف یک‌بارهٔ پیشنهاد")
        self.exercise_one.secondary_movement_type = section
        self.exercise_one.save(update_fields=["secondary_movement_type"])
        program = WorkoutProgram.objects.create(
            user=self.client_user,
            prescribed_by=self.admin,
            title="برنامهٔ مبنای مصرف",
        )
        session = WorkoutSession.objects.create(user=self.client_user, program=program)
        recommendation = TrainingAdjustmentRecommendation.objects.create(
            user=self.client_user,
            program=program,
            source_session=session,
            exercise=self.exercise_one,
            movement_name=self.exercise_one.name,
            recommendation_type=TrainingAdjustmentRecommendation.RecommendationType.INCREASE_LOAD,
            confidence=TrainingAdjustmentRecommendation.Confidence.HIGH,
            suggested_load_kg=Decimal("45"),
            reviewed_load_kg=Decimal("42.5"),
            rationale="پیشنهاد تأییدشده",
            status=TrainingAdjustmentRecommendation.Status.ACCEPTED,
            reviewed_by=self.admin,
            reviewed_at=timezone.now(),
        )
        service = AutomaticProgramService()
        args = {
            "difficulty": self.exercise_one.difficulty_level,
            "gender": None,
            "abnormalities": [],
            "sessions": 1,
            "movements": 1,
            "goal": "general",
            "secondary_movement_counts": {str(section.pk): 1},
            "athlete": self.client_user,
        }

        first_preview = service.generate(**args)
        self.assertEqual(first_preview["metadata"]["progression_recommendation_ids"], [recommendation.pk])
        program_day = WorkoutProgramDay.objects.create(program=program, name="روز مصرف پیشنهاد")
        WorkoutProgramExercise.objects.create(
            day=program_day,
            exercise=self.exercise_one,
            sets="3",
            reps="8",
            target_load_kg=Decimal("42.5"),
        )
        recommendation = TrainingAdjustmentRecommendation.objects.get(pk=recommendation.pk)
        self.assertEqual(
            TrainingRecommendationReviewService.mark_applied(recommendations=[recommendation], program=program),
            1,
        )
        recommendation.refresh_from_db()
        self.assertEqual(recommendation.applied_to_program_id, program.pk)
        self.assertIsNotNone(recommendation.applied_at)
        self.assertEqual(recommendation.applied_program_revision, program.revision)

        next_preview = service.generate(**args)
        self.assertEqual(next_preview["metadata"]["progression_recommendations_applied"], 0)
        self.assertEqual(next_preview["metadata"]["progression_recommendation_ids"], [])

    def test_admin_program_list_shows_feedback_separately_for_each_day(self):
        program = WorkoutProgram.objects.create(
            title="برنامه چهار جلسه‌ای",
            user=self.client_user,
            prescribed_by=self.admin,
            start_date=date(2026, 9, 1),
        )
        first_day = WorkoutProgramDay.objects.create(
            program=program,
            name="جلسه اول",
            order=1,
        )
        second_day = WorkoutProgramDay.objects.create(
            program=program,
            name="جلسه دوم",
            order=2,
        )
        WorkoutProgramFeedback.objects.create(program=program, day=first_day, difficulty=4)
        WorkoutProgramFeedback.objects.create(program=program, day=second_day, difficulty=8)

        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("register:admin_program_list", args=[self.client_user.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "جلسه اول")
        self.assertContains(response, "4/10")
        self.assertContains(response, "جلسه دوم")
        self.assertContains(response, "8/10")

    def test_admin_can_create_program_with_superset_and_corrective_movement(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("register:admin_program_add", args=[self.client_user.pk]),
            {
                "title": "برنامه حجم ماه اول",
                "start_date": "2026-09-01",
                "end_date": "2026-10-10",
                "is_published": "on",
                "notes": "تمرین با تمرکز بر فرم صحیح",
                "supplements_note": "آب کافی مصرف شود.",
                "warmup_notes": "",
                "cooldown_notes": "",
                "days_json": json.dumps(
                    [
                        {
                            "name": "روز سینه",
                            "notes": "استراحت بین ست‌ها رعایت شود.",
                            "items": [
                                {
                                    "exercise": self.exercise_one.pk,
                                    "superset_exercise": self.exercise_two.pk,
                                    "third_exercise": self.exercise_three.pk,
                                    "sets": "۳",
                                    "reps": "۸ تا ۱۲",
                                    "rest": "۶۰ ثانیه",
                                    "superset_sets": "۲",
                                    "superset_reps": "۱۰",
                                    "superset_rest": "۳۰ ثانیه",
                                    "third_sets": "۱",
                                    "third_reps": "۱۲",
                                    "third_rest": "۴۵ ثانیه",
                                    "note": "کنترل حرکت",
                                }
                            ],
                        }
                    ]
                ),
                "correctives_json": json.dumps(
                    [
                        {
                            "corrective_exercise": self.corrective.pk,
                            "phase": "warmup",
                            "sets": "۲",
                            "reps": "۱۰ تکرار",
                            "note": "بدون درد اجرا شود",
                        }
                    ]
                ),
            },
        )

        program = WorkoutProgram.objects.get(user=self.client_user)
        self.assertRedirects(
            response,
            reverse("register:admin_program_list", args=[self.client_user.pk]),
        )
        item = WorkoutProgramExercise.objects.get(day__program=program)
        self.assertEqual(item.exercise_id, self.exercise_one.pk)
        self.assertEqual(item.superset_exercise_id, self.exercise_two.pk)
        self.assertEqual(item.third_exercise_id, self.exercise_three.pk)
        self.assertEqual(item.superset_sets, "۲")
        self.assertEqual(item.superset_reps, "۱۰")
        self.assertEqual(item.superset_rest, "۳۰ ثانیه")
        self.assertEqual(item.third_sets, "۱")
        self.assertEqual(item.third_reps, "۱۲")
        self.assertEqual(item.third_rest, "۴۵ ثانیه")
        self.assertEqual(program.corrective_items.get().corrective_exercise_id, self.corrective.pk)
        self.assertEqual(program.prescribed_by_id, self.admin.pk)

    @patch("account.services.whatsapp_service.WhatsAppService.send_news_if_available", return_value="sent")
    def test_published_program_creates_athlete_news_notification(self, mocked_send):
        self.client.force_login(self.admin)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("register:admin_program_add", args=[self.client_user.pk]),
                {
                    "title": "برنامه خبری",
                    "start_date": "2026-09-01",
                    "is_published": "on",
                    "notes": "",
                    "supplements_note": "",
                    "warmup_notes": "",
                    "cooldown_notes": "",
                    "days_json": json.dumps(
                        [
                            {
                                "name": "روز اول",
                                "notes": "",
                                "items": [
                                    {
                                        "exercise": self.exercise_one.pk,
                                        "sets": "۳",
                                        "reps": "۱۰",
                                        "rest": "۶۰",
                                    }
                                ],
                            }
                        ]
                    ),
                    "correctives_json": "[]",
                },
            )

        self.assertRedirects(
            response,
            reverse("register:admin_program_list", args=[self.client_user.pk]),
        )
        notification = Notification.objects.get(user=self.client_user)
        self.assertEqual(notification.title, "برنامه بدنسازی جدید")
        self.assertIn("برنامه خبری", notification.message)
        mocked_send.assert_called_once()

    @patch("account.services.whatsapp_service.WhatsAppService.send_news_if_available", return_value="sent")
    def test_unpublished_program_does_not_create_athlete_news_notification(self, mocked_send):
        self.client.force_login(self.admin)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("register:admin_program_add", args=[self.client_user.pk]),
                {
                    "title": "پیش‌نویس برنامه",
                    "start_date": "2026-09-01",
                    "notes": "",
                    "supplements_note": "",
                    "warmup_notes": "",
                    "cooldown_notes": "",
                    "days_json": json.dumps(
                        [
                            {
                                "name": "روز اول",
                                "notes": "",
                                "items": [
                                    {
                                        "exercise": self.exercise_one.pk,
                                        "sets": "۳",
                                        "reps": "۱۰",
                                        "rest": "۶۰",
                                    }
                                ],
                            }
                        ]
                    ),
                    "correctives_json": "[]",
                },
            )

        self.assertRedirects(
            response,
            reverse("register:admin_program_list", args=[self.client_user.pk]),
        )
        self.assertFalse(Notification.objects.filter(user=self.client_user).exists())
        mocked_send.assert_not_called()

    def test_automatic_program_is_published_to_user_with_default_start_date(self):
        secondary_type = ExerciseSecondaryMovementType.objects.create(name="سینه خودکار")
        self.exercise_one.secondary_movement_type = secondary_type
        self.exercise_one.save(update_fields=["secondary_movement_type"])
        preview = AutomaticProgramService().generate(
            difficulty=self.exercise_one.difficulty_level,
            gender="",
            abnormalities=[],
            sessions=1,
            movements=1,
            goal="general",
            secondary_movement_counts={str(secondary_type.pk): 1},
        )

        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("register:admin_automatic_programming"),
            {
                "user": self.client_user.pk,
                "difficulty": self.exercise_one.difficulty_level.pk,
                "gender": "",
                "sessions_per_week": "1",
                "movements_per_session": "1",
                "goal": "general",
                "target_secondary_movement_types": [secondary_type.pk],
                "secondary_movement_counts_json": json.dumps({str(secondary_type.pk): 1}),
                "title": "برنامه خودکار منتشرشده",
                "is_published": "on",
                "start_date": "",
                "end_date": "",
                "notes": "",
                "warmup_notes": "",
                "cooldown_notes": "",
                "action": "save",
                "days_json": json.dumps(preview["days"]),
                "correctives_json": json.dumps(preview["correctives"]),
            },
        )

        program = WorkoutProgram.objects.get(title="برنامه خودکار منتشرشده")
        self.assertRedirects(
            response,
            reverse("register:admin_program_list", args=[self.client_user.pk]),
        )
        self.assertTrue(program.is_published)
        self.assertEqual(program.user_id, self.client_user.pk)
        self.assertEqual(program.start_date, timezone.localdate())
        self.assertFalse(program.requires_payment)
        self.assertEqual(program.price, 0)

        self.client.force_login(self.client_user)
        user_response = self.client.get(reverse("register:profile_workout_programs"))
        self.assertContains(user_response, program.title)

    def test_automatic_program_can_be_saved_as_paid_with_price(self):
        secondary_type = ExerciseSecondaryMovementType.objects.create(name="برنامه پولی خودکار")
        self.exercise_one.secondary_movement_type = secondary_type
        self.exercise_one.save(update_fields=["secondary_movement_type"])
        preview = AutomaticProgramService().generate(
            difficulty=self.exercise_one.difficulty_level,
            gender="",
            abnormalities=[],
            sessions=1,
            movements=1,
            goal="general",
            secondary_movement_counts={str(secondary_type.pk): 1},
        )

        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("register:admin_automatic_programming"),
            {
                "user": self.client_user.pk,
                "difficulty": self.exercise_one.difficulty_level.pk,
                "gender": "",
                "sessions_per_week": "1",
                "movements_per_session": "1",
                "goal": "general",
                "target_secondary_movement_types": [secondary_type.pk],
                "secondary_movement_counts_json": json.dumps({str(secondary_type.pk): 1}),
                "session_movement_targets_json": "[]",
                "title": "برنامه خودکار پولی",
                "is_published": "on",
                "requires_payment": "on",
                "price": "180000",
                "start_date": "2026-09-03",
                "end_date": "",
                "notes": "",
                "supplements_note": "",
                "warmup_notes": "",
                "cooldown_notes": "",
                "action": "save",
                "days_json": json.dumps(preview["days"]),
                "correctives_json": json.dumps(preview["correctives"]),
            },
        )

        program = WorkoutProgram.objects.get(title="برنامه خودکار پولی")
        self.assertRedirects(
            response,
            reverse("register:admin_program_list", args=[self.client_user.pk]),
        )
        self.assertTrue(program.requires_payment)
        self.assertEqual(program.price, 180000)

        self.client.force_login(self.client_user)
        user_response = self.client.get(reverse("register:profile_workout_programs"))
        self.assertContains(user_response, "نیازمند پرداخت")
        self.assertContains(user_response, "180000")

    def test_automatic_send_publishes_program_when_checkbox_is_not_submitted(self):
        secondary_type = ExerciseSecondaryMovementType.objects.create(name="ارسال خودکار")
        self.exercise_one.secondary_movement_type = secondary_type
        self.exercise_one.save(update_fields=["secondary_movement_type"])

        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("register:admin_automatic_programming"),
            {
                "user": self.client_user.pk,
                "difficulty": self.exercise_one.difficulty_level.pk,
                "gender": "",
                "sessions_per_week": "1",
                "movements_per_session": "1",
                "goal": "general",
                "target_secondary_movement_types": [secondary_type.pk],
                "secondary_movement_counts_json": json.dumps({str(secondary_type.pk): 1}),
                "session_movement_targets_json": "[]",
                "title": "برنامه ارسال خودکار",
                "start_date": "2026-09-03",
                "end_date": "",
                "notes": "",
                "supplements_note": "",
                "warmup_notes": "",
                "cooldown_notes": "",
                "action": "save",
                "days_json": json.dumps(
                    [
                        {
                            "name": "روز اول",
                            "items": [
                                {
                                    "exercise": self.exercise_one.pk,
                                    "sets": "۳",
                                    "reps": "۱۰",
                                }
                            ],
                        }
                    ]
                ),
                "correctives_json": "[]",
                # Deliberately omit is_published, as an unchecked checkbox does
                # in a browser form submission.
            },
        )

        program = WorkoutProgram.objects.get(title="برنامه ارسال خودکار")
        self.assertRedirects(
            response,
            reverse("register:admin_program_list", args=[self.client_user.pk]),
        )
        self.assertTrue(program.is_published)

        self.client.force_login(self.client_user)
        user_response = self.client.get(reverse("register:profile_workout_programs"))
        self.assertContains(user_response, program.title)
        plans_response = self.client.get(reverse("register:profile_plans"))
        self.assertContains(plans_response, program.title)

    def test_automatic_program_uses_session_targets_difficulty_and_abdomen_order(self):
        chest_type = ExerciseSecondaryMovementType.objects.create(name="سینه جلسه‌ای")
        abdomen_type = ExerciseSecondaryMovementType.objects.create(name="شکم جلسه‌ای")
        advanced, _ = ExerciseDifficultyLevel.objects.get_or_create(name="پیشرفته")
        advanced_chest = Exercise.objects.create(
            name="پرس سینه سطح انتخابی",
            primary_muscle=self.exercise_one.primary_muscle,
            body_part=self.exercise_one.body_part,
            movement_type=self.exercise_one.movement_type,
            joint_type=self.exercise_one.joint_type,
            power_type=self.exercise_one.power_type,
            difficulty_level=advanced,
            equipment_type=self.exercise_one.equipment_type,
            secondary_movement_type=chest_type,
        )
        easier_chest = Exercise.objects.create(
            name="پرس سینه سطح دیگر",
            primary_muscle=self.exercise_one.primary_muscle,
            body_part=self.exercise_one.body_part,
            movement_type=self.exercise_one.movement_type,
            joint_type=self.exercise_one.joint_type,
            power_type=self.exercise_one.power_type,
            difficulty_level=self.exercise_one.difficulty_level,
            equipment_type=self.exercise_one.equipment_type,
            secondary_movement_type=chest_type,
        )
        abdomen = Exercise.objects.create(
            name="کرانچ سطح انتخابی",
            primary_muscle=self.exercise_one.primary_muscle,
            body_part=self.exercise_one.body_part,
            movement_type=self.exercise_one.movement_type,
            joint_type=self.exercise_one.joint_type,
            power_type=self.exercise_one.power_type,
            difficulty_level=advanced,
            equipment_type=self.exercise_one.equipment_type,
            secondary_movement_type=abdomen_type,
        )

        preview = AutomaticProgramService().generate(
            difficulty=advanced,
            gender="",
            abnormalities=[],
            sessions=2,
            movements=1,
            goal="general",
            secondary_movement_counts={
                str(chest_type.pk): 1,
                str(abdomen_type.pk): 1,
            },
            session_movement_targets=[
                {
                    "movement_types": [abdomen_type.pk, chest_type.pk],
                    "counts": {
                        str(abdomen_type.pk): 1,
                        str(chest_type.pk): 1,
                    },
                },
                {
                    "movement_types": [chest_type.pk],
                    "counts": {str(chest_type.pk): 2},
                },
            ],
        )

        self.assertEqual(
            [item["exercise"] for item in preview["days"][0]["items"]],
            [advanced_chest.pk, abdomen.pk],
        )
        self.assertEqual(len(preview["days"][1]["items"]), 2)
        self.assertEqual(
            {item["exercise"] for item in preview["days"][1]["items"]},
            {advanced_chest.pk, easier_chest.pk},
        )

    def test_automatic_program_puts_power_first_and_compound_before_isolation(self):
        movement_type = ExerciseSecondaryMovementType.objects.create(name="ترتیب حرکات")
        power_type, _ = ExercisePowerType.objects.get_or_create(name="توانی")
        single_joint, _ = ExerciseJointType.objects.get_or_create(name="تک مفصلی")

        power_isolation = Exercise.objects.create(
            name="حرکت توان انفجاری",
            primary_muscle=self.exercise_one.primary_muscle,
            body_part=self.exercise_one.body_part,
            movement_type=self.exercise_one.movement_type,
            joint_type=single_joint,
            power_type=power_type,
            difficulty_level=self.exercise_one.difficulty_level,
            equipment_type=self.exercise_one.equipment_type,
            secondary_movement_type=movement_type,
        )
        compound = Exercise.objects.create(
            name="حرکت چندمفصلی",
            primary_muscle=self.exercise_one.primary_muscle,
            body_part=self.exercise_one.body_part,
            movement_type=self.exercise_one.movement_type,
            joint_type=self.exercise_one.joint_type,
            power_type=self.exercise_one.power_type,
            difficulty_level=self.exercise_one.difficulty_level,
            equipment_type=self.exercise_one.equipment_type,
            secondary_movement_type=movement_type,
        )
        isolation = Exercise.objects.create(
            name="حرکت تک‌مفصلی",
            primary_muscle=self.exercise_one.primary_muscle,
            body_part=self.exercise_one.body_part,
            movement_type=self.exercise_one.movement_type,
            joint_type=single_joint,
            power_type=self.exercise_one.power_type,
            difficulty_level=self.exercise_one.difficulty_level,
            equipment_type=self.exercise_one.equipment_type,
            secondary_movement_type=movement_type,
        )

        preview = AutomaticProgramService().generate(
            difficulty=self.exercise_one.difficulty_level,
            gender="",
            abnormalities=[],
            sessions=1,
            movements=3,
            goal="general",
            secondary_movement_counts={str(movement_type.pk): 3},
        )

        selected_ids = [item["exercise"] for item in preview["days"][0]["items"]]
        self.assertEqual(selected_ids, [power_isolation.pk, compound.pk, isolation.pk])

    def test_automatic_program_varies_goal_prescriptions_from_database(self):
        movement_type = ExerciseSecondaryMovementType.objects.create(name="تنوع نسخه")
        for exercise in (self.exercise_one, self.exercise_two, self.exercise_three):
            exercise.secondary_movement_type = movement_type
            exercise.save(update_fields=["secondary_movement_type"])

        ExerciseSetType.objects.create(set_count="SET-A", goal="hypertrophy")
        ExerciseSetType.objects.create(set_count="SET-B", goal="hypertrophy")
        ExerciseRepetitionType.objects.create(reps="REP-A", goal="hypertrophy")
        ExerciseRepetitionType.objects.create(reps="REP-B", goal="hypertrophy")

        preview = AutomaticProgramService().generate(
            difficulty=self.exercise_one.difficulty_level,
            gender="",
            abnormalities=[],
            sessions=1,
            movements=3,
            goal="hypertrophy",
            secondary_movement_counts={str(movement_type.pk): 3},
        )
        items = preview["days"][0]["items"]
        database_sets = set(
            ExerciseSetType.objects.filter(goal="hypertrophy").values_list("set_count", flat=True)
        )
        database_reps = set(
            ExerciseRepetitionType.objects.filter(goal="hypertrophy").values_list("reps", flat=True)
        )

        self.assertGreater(len({item["sets"] for item in items}), 1)
        self.assertGreater(len({item["reps"] for item in items}), 1)
        self.assertTrue({item["sets"] for item in items}.issubset(database_sets))
        self.assertTrue({item["reps"] for item in items}.issubset(database_reps))

    def test_automatic_program_varies_repeated_body_part_sessions_before_repeating(self):
        chest_type = ExerciseSecondaryMovementType.objects.create(name="سینه تنوع هفتگی")
        exercises = [self.exercise_one, self.exercise_two, self.exercise_three]
        for exercise in exercises:
            exercise.secondary_movement_type = chest_type
            exercise.save(update_fields=["secondary_movement_type"])

        preview = AutomaticProgramService().generate(
            difficulty=self.exercise_one.difficulty_level,
            gender="",
            abnormalities=[],
            sessions=3,
            movements=1,
            goal="volume",
            secondary_movement_counts={str(chest_type.pk): 1},
            session_movement_targets=[
                {"movement_types": [chest_type.pk], "counts": {str(chest_type.pk): 1}},
                {"movement_types": [chest_type.pk], "counts": {str(chest_type.pk): 1}},
                {"movement_types": [chest_type.pk], "counts": {str(chest_type.pk): 1}},
            ],
        )

        selected_ids = [day["items"][0]["exercise"] for day in preview["days"]]
        self.assertEqual(len(selected_ids), 3)
        self.assertEqual(len(set(selected_ids)), 3)

    def test_automatic_program_reuses_a_movement_across_sessions_when_pool_is_small(self):
        chest_type = ExerciseSecondaryMovementType.objects.create(name="سینه ظرفیت محدود")
        self.exercise_one.secondary_movement_type = chest_type
        self.exercise_one.save(update_fields=["secondary_movement_type"])

        preview = AutomaticProgramService().generate(
            difficulty=self.exercise_one.difficulty_level,
            gender="",
            abnormalities=[],
            sessions=2,
            movements=1,
            goal="general",
            secondary_movement_counts={str(chest_type.pk): 1},
            session_movement_targets=[
                {"movement_types": [chest_type.pk], "counts": {str(chest_type.pk): 1}},
                {"movement_types": [chest_type.pk], "counts": {str(chest_type.pk): 1}},
            ],
        )

        self.assertEqual(len(preview["days"]), 2)
        self.assertEqual(preview["days"][0]["items"][0]["exercise"], self.exercise_one.pk)
        self.assertEqual(preview["days"][1]["items"][0]["exercise"], self.exercise_one.pk)

    def test_automatic_program_does_not_repeat_a_movement_inside_one_session(self):
        chest_type = ExerciseSecondaryMovementType.objects.create(name="سینه بدون تکرار جلسه")
        self.exercise_one.secondary_movement_type = chest_type
        self.exercise_one.save(update_fields=["secondary_movement_type"])

        preview = AutomaticProgramService().generate(
            difficulty=self.exercise_one.difficulty_level,
            gender="",
            abnormalities=[],
            sessions=1,
            movements=2,
            goal="general",
            secondary_movement_counts={str(chest_type.pk): 2},
            session_movement_targets=[
                {"movement_types": [chest_type.pk], "counts": {str(chest_type.pk): 2}},
            ],
        )

        item_ids = [item["exercise"] for item in preview["days"][0]["items"]]
        self.assertEqual(item_ids, [self.exercise_one.pk])

    def test_automatic_program_allows_only_selected_or_easier_difficulty_levels(self):
        movement_type = ExerciseSecondaryMovementType.objects.create(name="سطح‌بندی جلسه‌ای")
        intermediate, _ = ExerciseDifficultyLevel.objects.get_or_create(name="متوسط")
        advanced, _ = ExerciseDifficultyLevel.objects.get_or_create(name="پیشرفته")

        intermediate_exercise = Exercise.objects.create(
            name="حرکت متوسط",
            primary_muscle=self.exercise_one.primary_muscle,
            body_part=self.exercise_one.body_part,
            movement_type=self.exercise_one.movement_type,
            joint_type=self.exercise_one.joint_type,
            power_type=self.exercise_one.power_type,
            difficulty_level=intermediate,
            equipment_type=self.exercise_one.equipment_type,
            secondary_movement_type=movement_type,
        )
        beginner_exercise = Exercise.objects.create(
            name="حرکت مبتدی",
            primary_muscle=self.exercise_one.primary_muscle,
            body_part=self.exercise_one.body_part,
            movement_type=self.exercise_one.movement_type,
            joint_type=self.exercise_one.joint_type,
            power_type=self.exercise_one.power_type,
            difficulty_level=self.exercise_one.difficulty_level,
            equipment_type=self.exercise_one.equipment_type,
            secondary_movement_type=movement_type,
        )
        service = AutomaticProgramService()
        base_kwargs = {
            "gender": "",
            "abnormalities": [],
            "sessions": 1,
            "movements": 3,
            "goal": "general",
            "secondary_movement_counts": {str(movement_type.pk): 3},
        }

        advanced_preview = service.generate(difficulty=advanced, **base_kwargs)
        advanced_exercise_ids = [
            item["exercise"] for item in advanced_preview["days"][0]["items"]
        ]
        self.assertIn(
            beginner_exercise.pk,
            advanced_exercise_ids,
        )
        self.assertIn(
            intermediate_exercise.pk,
            advanced_exercise_ids,
        )

        advanced_exercise = Exercise.objects.create(
            name="حرکت پیشرفته",
            primary_muscle=self.exercise_one.primary_muscle,
            body_part=self.exercise_one.body_part,
            movement_type=self.exercise_one.movement_type,
            joint_type=self.exercise_one.joint_type,
            power_type=self.exercise_one.power_type,
            difficulty_level=advanced,
            equipment_type=self.exercise_one.equipment_type,
            secondary_movement_type=movement_type,
        )
        self.assertNotIn(
            advanced_exercise.pk,
            [
                item["exercise"]
                for item in service.generate(
                    difficulty=intermediate,
                    **base_kwargs,
                )["days"][0]["items"]
            ],
        )

        beginner_preview = service.generate(
            difficulty=self.exercise_one.difficulty_level,
            **base_kwargs,
        )
        self.assertTrue(
            all(
                item["exercise"] == beginner_exercise.pk
                for item in beginner_preview["days"][0]["items"]
            )
        )

    def test_automatic_program_uses_database_goal_aliases(self):
        secondary_type = ExerciseSecondaryMovementType.objects.create(name="هدف حجم")
        self.exercise_one.secondary_movement_type = secondary_type
        self.exercise_one.save(update_fields=["secondary_movement_type"])
        ExerciseSetType.objects.create(set_count="DB-SET", goal="hypertrophy")
        ExerciseRepetitionType.objects.create(reps="DB-REPS", goal="hypertrophy")
        ExerciseRestType.objects.create(rest_time="DB-REST", goal="hypertrophy")

        preview = AutomaticProgramService().generate(
            difficulty=self.exercise_one.difficulty_level,
            gender="",
            abnormalities=[],
            sessions=1,
            movements=1,
            goal="volume",
            secondary_movement_counts={str(secondary_type.pk): 1},
        )

        item = preview["days"][0]["items"][0]
        self.assertEqual(item["sets"], "DB-SET")
        self.assertEqual(item["reps"], "DB-REPS")
        self.assertEqual(item["rest"], "DB-REST")

    def test_automatic_program_uses_selected_athlete_goal_for_prescription(self):
        secondary_type = ExerciseSecondaryMovementType.objects.create(name="هدف قدرت منتخب")
        self.exercise_one.secondary_movement_type = secondary_type
        self.exercise_one.save(update_fields=["secondary_movement_type"])
        ExerciseSetType.objects.create(set_count="۵ ست قدرت", goal="strength")
        ExerciseRepetitionType.objects.create(reps="۳ تا ۵ قدرت", goal="strength")
        ExerciseRestType.objects.create(rest_time="۱۸۰ ثانیه قدرت", goal="strength")

        preview = AutomaticProgramService().generate(
            difficulty=self.exercise_one.difficulty_level,
            gender=None,
            abnormalities=[],
            sessions=1,
            movements=1,
            goal="volume",
            secondary_movement_counts={str(secondary_type.pk): 1},
            athlete=self.client_user,
            training_goal="strength",
            training_goal_source="admin",
        )

        item = preview["days"][0]["items"][0]
        self.assertEqual(item["goal"], "strength")
        self.assertEqual(item["sets"], "۵ ست قدرت")
        self.assertEqual(item["reps"], "۳ تا ۵ قدرت")
        self.assertEqual(preview["metadata"]["selected_training_goal"], "strength")
        self.assertEqual(preview["metadata"]["training_goal_source"], "admin")
        self.assertEqual(preview["metadata"]["weekly_volume_target"]["goal_band"], "strength_power")

    def test_automatic_view_uses_profile_goal_when_admin_override_is_blank(self):
        secondary_type = ExerciseSecondaryMovementType.objects.create(name="هدف پروفایل حجم")
        self.exercise_one.secondary_movement_type = secondary_type
        self.exercise_one.save(update_fields=["secondary_movement_type"])
        AthleteTrainingProfile.objects.create(
            user=self.client_user,
            primary_goal="hypertrophy",
            experience_years=3,
        )
        ExerciseSetType.objects.create(set_count="۴ ست حجم پروفایل", goal="hypertrophy")
        ExerciseRepetitionType.objects.create(reps="۸ تا ۱۲ حجم پروفایل", goal="hypertrophy")
        ExerciseRestType.objects.create(rest_time="۹۰ ثانیه حجم پروفایل", goal="hypertrophy")
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("register:admin_automatic_programming"),
            {
                "user": self.client_user.pk,
                "athlete_goal": "",
                "difficulty": self.exercise_one.difficulty_level.pk,
                "sessions_per_week": "1",
                "movements_per_session": "1",
                "goal": "general",
                "target_secondary_movement_types": [secondary_type.pk],
                "secondary_movement_counts_json": json.dumps({str(secondary_type.pk): 1}),
                "session_movement_targets_json": "[]",
                "title": "برنامه با هدف پروفایل",
                "action": "generate",
            },
        )

        self.assertEqual(response.status_code, 200)
        item = response.context["preview"]["days"][0]["items"][0]
        metadata = response.context["preview"]["metadata"]
        self.assertEqual(item["goal"], "hypertrophy")
        self.assertEqual(item["sets"], "۴ ست حجم پروفایل")
        self.assertEqual(metadata["selected_training_goal"], "hypertrophy")
        self.assertEqual(metadata["training_goal_source"], "athlete_profile")

    def test_automatic_program_form_loads_goals_from_database(self):
        ExerciseGoal.objects.create(name="هدف سفارشی", name_en="custom-goal")

        choices = dict(AutomaticProgrammingForm().fields["goal"].choices)

        self.assertIn("custom_goal", choices)
        self.assertEqual(choices["custom_goal"], "هدف سفارشی")
        self.assertIn("strength", choices)
        self.assertIn("athlete_goal", AutomaticProgrammingForm().fields)
        athlete_goal_choices = dict(AutomaticProgrammingForm().fields["athlete_goal"].choices)
        self.assertEqual(athlete_goal_choices["hypertrophy"], choices["hypertrophy"])
        self.assertNotIn("gender", AutomaticProgrammingForm().fields)

    def test_automatic_view_accepts_per_session_targets_and_searches_athletes(self):
        secondary_type = ExerciseSecondaryMovementType.objects.create(name="هدف جلسه")
        self.exercise_one.secondary_movement_type = secondary_type
        self.exercise_one.save(update_fields=["secondary_movement_type"])
        self.client_user.first_name = "علی"
        self.client_user.last_name = "ورزشکار"
        self.client_user.save(update_fields=["first_name", "last_name"])

        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("register:admin_automatic_programming"),
            {
                "user": self.client_user.pk,
                "difficulty": self.exercise_one.difficulty_level.pk,
                "gender": "",
                "sessions_per_week": "2",
                "movements_per_session": "1",
                "goal": "general",
                "target_secondary_movement_types": [secondary_type.pk],
                "secondary_movement_counts_json": json.dumps({str(secondary_type.pk): 1}),
                "session_movement_targets_json": json.dumps(
                    [
                        {
                            "movement_types": [str(secondary_type.pk)],
                            "counts": {str(secondary_type.pk): 1},
                        },
                        {
                            "movement_types": [str(secondary_type.pk)],
                            "counts": {str(secondary_type.pk): 1},
                        },
                    ]
                ),
                "title": "برنامه جلسه‌ای",
                "action": "generate",
            },
        )

        self.assertEqual(response.status_code, 200)
        # Weekly exposure balancing can repeat a movement when the library has
        # no alternative that matches the selected target.
        self.assertEqual(len(response.context["preview"]["days"]), 2)
        self.assertEqual(
            response.context["preview"]["days"][0]["items"][0]["exercise"],
            response.context["preview"]["days"][1]["items"][0]["exercise"],
        )
        self.assertContains(response, "automatic-athlete-search")
        self.assertContains(response, self.client_user.phone)
        self.assertContains(response, "id_session_movement_targets_json")
        self.assertContains(response, "علی ورزشکار")

    def test_admin_program_editor_reads_movements_from_database(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("register:admin_program_add", args=[self.client_user.pk])
        )

        self.assertEqual(response.status_code, 200)
        exercise_names = {item["name"] for item in response.context["exercise_catalog"]}
        corrective_names = {item["name"] for item in response.context["corrective_catalog"]}
        self.assertIn(self.exercise_one.name, exercise_names)
        self.assertIn(self.exercise_two.name, exercise_names)
        self.assertIn(self.exercise_three.name, exercise_names)
        self.assertIn(self.corrective.name, corrective_names)
        self.assertEqual(response.context["set_catalog"][0]["value"], "۳")
        self.assertIn({"value": "۸ تا ۱۲", "label": "۸ تا ۱۲"}, response.context["repetition_catalog"])
        self.assertIn({"value": "۶۰ ثانیه", "label": "۶۰ ثانیه"}, response.context["rest_catalog"])
        self.assertContains(response, "برنامه برای ورزشکار")

    def test_invalid_superset_is_rejected_without_creating_program(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("register:admin_program_add", args=[self.client_user.pk]),
            {
                "title": "برنامه نامعتبر",
                "start_date": "2026-09-01",
                "is_published": "on",
                "days_json": json.dumps(
                    [
                        {
                            "name": "روز اول",
                            "items": [
                                {
                                    "exercise": self.exercise_one.pk,
                                    "superset_exercise": self.exercise_one.pk,
                                    "sets": "۳",
                                    "reps": "۱۰",
                                }
                            ],
                        }
                    ]
                ),
                "correctives_json": "[]",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkoutProgram.objects.filter(title="برنامه نامعتبر").exists())
        self.assertContains(response, "متفاوت باشد")

    def test_user_sees_published_program_table_and_movement_links(self):
        program = WorkoutProgram.objects.create(
            user=self.client_user,
            prescribed_by=self.admin,
            title="برنامه قابل مشاهده",
            start_date=date(2026, 9, 1),
            is_published=True,
        )
        day = WorkoutProgramDay.objects.create(program=program, name="روز اول", order=1)
        WorkoutProgramExercise.objects.create(
            day=day,
            exercise=self.exercise_one,
            superset_exercise=self.exercise_two,
            third_exercise=self.exercise_three,
            sets="۳",
            reps="۱۰",
            rest="۶۰",
        )
        WorkoutProgramCorrective.objects.create(
            program=program,
            corrective_exercise=self.corrective,
            phase="cooldown",
            reps="۳۰ ثانیه",
        )
        self.client.force_login(self.client_user)

        response = self.client.get(reverse("register:profile_workout_programs"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, program.title)
        self.assertContains(response, self.exercise_one.name)
        self.assertContains(response, self.exercise_two.name)
        self.assertContains(response, self.exercise_three.name)
        self.assertContains(response, self.corrective.name)
        self.assertContains(
            response,
            reverse(
                "register:profile_workout_movement",
                args=["exercise", self.exercise_one.pk],
            ),
        )
        self.assertContains(response, "برنامه بدنسازی")

    def test_draft_program_is_hidden_from_user(self):
        WorkoutProgram.objects.create(
            user=self.client_user,
            prescribed_by=self.admin,
            title="پیش‌نویس مربی",
            start_date=date(2026, 9, 1),
            is_published=False,
        )
        self.client.force_login(self.client_user)

        response = self.client.get(reverse("register:profile_workout_programs"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "پیش‌نویس مربی")

    def test_user_can_open_linked_movement_but_not_unlinked_movement(self):
        program = WorkoutProgram.objects.create(
            user=self.client_user,
            prescribed_by=self.admin,
            title="برنامه حرکت",
            start_date=date(2026, 9, 1),
            is_published=True,
        )
        day = WorkoutProgramDay.objects.create(program=program, name="روز اول", order=1)
        WorkoutProgramExercise.objects.create(
            day=day,
            exercise=self.exercise_one,
            sets="۳",
            reps="۱۰",
        )
        self.client.force_login(self.client_user)

        response = self.client.get(
            reverse(
                "register:profile_workout_movement",
                args=["exercise", self.exercise_one.pk],
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.exercise_one.name)

        WorkoutProgramExercise.objects.filter(day=day).update(superset_exercise=self.exercise_two)
        response = self.client.get(
            reverse(
                "register:profile_workout_movement",
                args=["exercise", self.exercise_two.pk],
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.exercise_two.name)

        WorkoutProgramExercise.objects.filter(day=day).update(third_exercise=self.exercise_three)
        response = self.client.get(
            reverse(
                "register:profile_workout_movement",
                args=["exercise", self.exercise_three.pk],
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.exercise_three.name)

        response = self.client.get(
            reverse(
                "register:profile_workout_movement",
                args=["exercise", "999999"],
            )
        )
        self.assertEqual(response.status_code, 404)

    def test_pdf_download_is_available_to_admin_and_owner(self):
        program = WorkoutProgram.objects.create(
            user=self.client_user,
            prescribed_by=self.admin,
            title="برنامه PDF",
            start_date=date(2026, 9, 1),
            is_published=True,
        )
        day = WorkoutProgramDay.objects.create(program=program, name="روز اول", order=1)
        WorkoutProgramExercise.objects.create(
            day=day,
            exercise=self.exercise_one,
            sets="۳",
            reps="۱۰",
        )

        self.client.force_login(self.admin)
        admin_response = self.client.get(
            reverse("register:admin_program_pdf", args=[self.client_user.pk, program.pk])
        )
        self.assertEqual(admin_response.status_code, 200)
        self.assertEqual(admin_response["Content-Type"], "application/pdf")
        self.assertTrue(admin_response.content.startswith(b"%PDF"))

        self.client.force_login(self.client_user)
        user_response = self.client.get(
            reverse("register:profile_workout_program_pdf", args=[program.pk])
        )
        self.assertEqual(user_response.status_code, 200)
        self.assertEqual(user_response["Content-Type"], "application/pdf")
        self.assertTrue(user_response.content.startswith(b"%PDF"))

    def test_pdf_move_name_links_to_stored_exercise_media(self):
        Exercise.objects.filter(pk=self.exercise_one.pk).update(
            media="exercises/pdf-bench-press.mp4"
        )

        program = WorkoutProgram.objects.create(
            user=self.client_user,
            prescribed_by=self.admin,
            title="برنامه لینک حرکت",
            start_date=date(2026, 9, 1),
            is_published=True,
        )
        day = WorkoutProgramDay.objects.create(program=program, name="روز اول", order=1)
        WorkoutProgramExercise.objects.create(
            day=day,
            exercise=self.exercise_one,
            sets="۳",
            reps="۱۰",
        )
        self.client.force_login(self.client_user)

        response = self.client.get(
            reverse("register:profile_workout_program_pdf", args=[program.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            b"http://testserver/media-files/exercises/pdf-bench-press.mp4",
            response.content,
        )

    def test_user_can_save_best_set_values_and_program_difficulty(self):
        program = WorkoutProgram.objects.create(
            user=self.client_user,
            prescribed_by=self.admin,
            title="برنامه ثبت رکورد",
            start_date=date(2026, 9, 1),
            is_published=True,
        )
        day = WorkoutProgramDay.objects.create(program=program, name="روز اول", order=1)
        item = WorkoutProgramExercise.objects.create(
            day=day,
            exercise=self.exercise_one,
            sets="۳",
            reps="۱۰",
        )
        self.client.force_login(self.client_user)

        response = self.client.post(
            reverse(
                "register:profile_workout_program_performance",
                args=[program.pk],
            ),
            {
                f"performance_{item.pk}_main_mode": "weight",
                f"performance_{item.pk}_main_weight_set_1": "40",
                f"performance_{item.pk}_main_weight_set_2": "45",
                "difficulty": "8",
            },
        )

        self.assertRedirects(response, reverse("register:profile_workout_programs"))
        self.assertEqual(
            WorkoutPerformanceRecord.objects.filter(
                user=self.client_user,
                exercise=self.exercise_one,
                repetitions="10",
                mode=WorkoutPerformanceRecord.Mode.WEIGHT,
            ).count(),
            2,
        )
        self.assertEqual(
            WorkoutPerformanceRecord.objects.get(
                user=self.client_user,
                exercise=self.exercise_one,
                repetitions="10",
                mode=WorkoutPerformanceRecord.Mode.WEIGHT,
                set_number=2,
            ).value,
            45,
        )
        self.assertEqual(
            WorkoutProgramFeedback.objects.get(program=program).difficulty,
            8,
        )

        self.client.post(
            reverse(
                "register:profile_workout_program_performance",
                args=[program.pk],
            ),
            {
                f"performance_{item.pk}_main_mode": "weight",
                f"performance_{item.pk}_main_weight_set_1": "35",
                f"performance_{item.pk}_main_weight_set_2": "44",
                "difficulty": "6",
            },
        )
        self.assertEqual(
            WorkoutPerformanceRecord.objects.get(
                user=self.client_user,
                exercise=self.exercise_one,
                repetitions="10",
                mode=WorkoutPerformanceRecord.Mode.WEIGHT,
                set_number=1,
            ).value,
            40,
        )
        self.assertEqual(
            WorkoutPerformanceRecord.objects.get(
                user=self.client_user,
                exercise=self.exercise_one,
                repetitions="10",
                mode=WorkoutPerformanceRecord.Mode.WEIGHT,
                set_number=2,
            ).value,
            45,
        )
        self.assertEqual(
            WorkoutProgramFeedback.objects.get(program=program).difficulty,
            6,
        )

        self.client.post(
            reverse(
                "register:profile_workout_program_performance",
                args=[program.pk],
            ),
            {
                f"performance_{item.pk}_main_mode": "body_weight",
                f"performance_{item.pk}_main_body_weight_set_1": "۷۵",
                "difficulty": "6",
            },
        )
        self.client.post(
            reverse(
                "register:profile_workout_program_performance",
                args=[program.pk],
            ),
            {
                f"performance_{item.pk}_main_mode": "time",
                f"performance_{item.pk}_main_time_set_1": "۶۰",
                "difficulty": "6",
            },
        )
        self.assertEqual(
            WorkoutPerformanceRecord.objects.get(
                user=self.client_user,
                exercise=self.exercise_one,
                repetitions="10",
                mode=WorkoutPerformanceRecord.Mode.BODY_WEIGHT,
                set_number=1,
            ).value,
            75,
        )
        self.assertEqual(
            WorkoutPerformanceRecord.objects.get(
                user=self.client_user,
                exercise=self.exercise_one,
                repetitions="10",
                mode=WorkoutPerformanceRecord.Mode.TIME,
                set_number=1,
            ).value,
            60,
        )

    def test_analysis_performance_chart_uses_recorded_dates(self):
        WorkoutPerformanceRecord.objects.create(
            user=self.client_user,
            exercise=self.exercise_one,
            repetitions="10",
            mode=WorkoutPerformanceRecord.Mode.WEIGHT,
            set_number=1,
            value=Decimal("40"),
            recorded_on=date(2026, 9, 1),
        )
        WorkoutPerformanceRecord.objects.create(
            user=self.client_user,
            exercise=self.exercise_one,
            repetitions="10",
            mode=WorkoutPerformanceRecord.Mode.WEIGHT,
            set_number=1,
            value=Decimal("45"),
            recorded_on=date(2026, 9, 2),
        )

        data = ProfileService().get_performance_chart_data(self.client_user)

        self.assertTrue(data["performance_has_data"])
        series = data["performance_exercises"][0]["modes"]["weight"]
        self.assertEqual(series["values"], [40.0, 45.0])
        self.assertEqual(len(series["labels"]), 2)

    def test_performance_json_validation_returns_errors_without_saving(self):
        program = WorkoutProgram.objects.create(
            user=self.client_user,
            prescribed_by=self.admin,
            title="برنامه اعتبارسنجی",
            start_date=date(2026, 9, 1),
            is_published=True,
        )
        day = WorkoutProgramDay.objects.create(program=program, name="روز اول", order=1)
        item = WorkoutProgramExercise.objects.create(
            day=day,
            exercise=self.exercise_one,
            sets="۲",
            reps="۱۰",
        )
        self.client.force_login(self.client_user)
        url = reverse(
            "register:profile_workout_program_performance",
            args=[program.pk],
        )

        invalid_response = self.client.post(
            url,
            {
                f"performance_{item.pk}_main_mode": "weight",
                f"performance_{item.pk}_main_weight_set_1": "abc",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(invalid_response.status_code, 400)
        self.assertFalse(invalid_response.json()["ok"])
        self.assertTrue(invalid_response.json()["errors"])
        self.assertFalse(
            WorkoutPerformanceRecord.objects.filter(user=self.client_user).exists()
        )

        empty_response = self.client.post(
            url,
            {"difficulty": ""},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(empty_response.status_code, 400)
        self.assertIn(
            "هیچ رکوردی",
            empty_response.json()["errors"][0]["message"],
        )

        best_record_response = self.client.post(
            url,
            {f"best_record_{self.exercise_one.pk}": "abc"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(best_record_response.status_code, 400)
        self.assertEqual(
            best_record_response.json()["errors"][0]["field"],
            f"best_record_{self.exercise_one.pk}",
        )
        self.assertFalse(
            WorkoutPerformanceRecord.objects.filter(user=self.client_user).exists()
        )

    def test_performance_mode_is_saved_per_movement_instance(self):
        program = WorkoutProgram.objects.create(
            user=self.client_user,
            prescribed_by=self.admin,
            title="برنامه حالت‌های جداگانه",
            start_date=date(2026, 9, 1),
            is_published=True,
        )
        day = WorkoutProgramDay.objects.create(program=program, name="روز اول", order=1)
        first_item = WorkoutProgramExercise.objects.create(
            day=day,
            exercise=self.exercise_one,
            sets="۱",
            reps="۱۰",
        )
        second_item = WorkoutProgramExercise.objects.create(
            day=day,
            exercise=self.exercise_one,
            sets="۱",
            reps="۱۰",
        )
        self.client.force_login(self.client_user)

        response = self.client.post(
            reverse(
                "register:profile_workout_program_performance",
                args=[program.pk],
            ),
            {
                f"performance_{first_item.pk}_main_mode": "weight",
                f"performance_{first_item.pk}_main_weight_set_1": "40",
                f"performance_{second_item.pk}_main_mode": "time",
                f"performance_{second_item.pk}_main_time_set_1": "60",
            },
        )

        self.assertRedirects(response, reverse("register:profile_workout_programs"))
        first_item.refresh_from_db()
        second_item.refresh_from_db()
        self.assertEqual(first_item.performance_modes, {"main": "weight"})
        self.assertEqual(second_item.performance_modes, {"main": "time"})

        response = self.client.get(reverse("register:profile_workout_programs"))
        self.assertEqual(response.status_code, 200)
        rendered_program = next(
            rendered_program
            for rendered_program in response.context["programs"]
            if rendered_program.pk == program.pk
        )
        rendered_modes = {
            movement["input_prefix"]: movement["selected_mode"]
            for item in rendered_program.days.all()[0].items.all()
            for movement in item.performance_movements
        }
        self.assertEqual(rendered_modes[f"performance_{first_item.pk}_main"], "weight")
        self.assertEqual(rendered_modes[f"performance_{second_item.pk}_main"], "time")

    def test_same_movement_has_independent_actual_set_inputs_for_each_table_row(self):
        program = WorkoutProgram.objects.create(
            user=self.client_user,
            prescribed_by=self.admin,
            title="برنامه حرکت تکراری",
            start_date=date(2026, 9, 1),
            is_published=True,
        )
        day = WorkoutProgramDay.objects.create(program=program, name="روز اول", order=1)
        first_item = WorkoutProgramExercise.objects.create(
            day=day,
            exercise=self.exercise_one,
            sets="۲",
            reps="۱۰",
        )
        WorkoutProgramExercise.objects.create(
            day=day,
            exercise=self.exercise_one,
            sets="۳",
            reps="۱۰",
        )
        self.client.force_login(self.client_user)

        response = self.client.get(reverse("register:profile_workout_programs"))
        self.assertEqual(response.status_code, 200)
        rendered_program = next(
            rendered_program
            for rendered_program in response.context["programs"]
            if rendered_program.pk == program.pk
        )
        self.assertEqual(
            len(rendered_program.days.all()[0].performance_movements),
            1,
        )
        self.assertContains(
            response,
            f'name="session_{first_item.pk}_main_1_actual_load_kg"',
        )
        second_item = rendered_program.days.all()[0].items.all()[1]
        self.assertContains(
            response,
            f'name="session_{second_item.pk}_main_1_actual_load_kg"',
        )

        response = self.client.post(
            reverse(
                "register:profile_workout_program_performance",
                args=[program.pk],
            ),
            {
                "day_id": str(day.pk),
                f"performance_{first_item.pk}_main_mode": "weight",
                f"performance_{first_item.pk}_main_weight_set_1": "40",
                f"performance_{first_item.pk}_main_weight_set_2": "45",
                "difficulty": "7",
            },
        )

        self.assertRedirects(response, reverse("register:profile_workout_programs"))
        self.assertEqual(
            WorkoutPerformanceRecord.objects.filter(
                user=self.client_user,
                exercise=self.exercise_one,
                repetitions="10",
                mode=WorkoutPerformanceRecord.Mode.WEIGHT,
            ).count(),
            2,
        )
        self.assertEqual(
            WorkoutProgramFeedback.objects.get(
                program=program,
                day=day,
            ).difficulty,
            7,
        )

    def test_same_repetition_text_variants_are_grouped_to_one_performance_row(self):
        program = WorkoutProgram.objects.create(
            user=self.client_user,
            prescribed_by=self.admin,
            title="برنامه حرکت با فرمت تکرار متفاوت",
            start_date=date(2026, 9, 1),
            is_published=True,
        )
        day = WorkoutProgramDay.objects.create(program=program, name="روز اول", order=1)
        WorkoutProgramExercise.objects.create(
            day=day,
            exercise=self.exercise_one,
            sets="۲",
            reps="10 - 12",
        )
        WorkoutProgramExercise.objects.create(
            day=day,
            exercise=self.exercise_one,
            sets="۲",
            reps="10-12",
        )
        self.client.force_login(self.client_user)

        response = self.client.get(reverse("register:profile_workout_programs"))
        self.assertEqual(response.status_code, 200)
        rendered_program = next(
            rendered_program
            for rendered_program in response.context["programs"]
            if rendered_program.pk == program.pk
        )
        self.assertEqual(
            len(rendered_program.days.all()[0].performance_movements),
            1,
        )

    def test_program_rows_show_actual_set_fields_when_best_record_exists(self):
        program = WorkoutProgram.objects.create(
            user=self.client_user,
            prescribed_by=self.admin,
            title="برنامه سابق",
            start_date=date(2026, 8, 1),
            is_published=True,
        )
        old_day = WorkoutProgramDay.objects.create(program=program, name="روز اول", order=1)
        WorkoutProgramExercise.objects.create(
            day=old_day,
            exercise=self.exercise_one,
            sets="۲",
            reps="۸",
        )
        WorkoutBestRecord.objects.create(
            user=self.client_user,
            exercise=self.exercise_one,
            value=50,
        )

        later_program = WorkoutProgram.objects.create(
            user=self.client_user,
            prescribed_by=self.admin,
            title="برنامه جدید",
            start_date=date(2026, 9, 1),
            is_published=True,
        )
        later_day = WorkoutProgramDay.objects.create(
            program=later_program,
            name="روز جدید",
            order=1,
        )
        WorkoutProgramExercise.objects.create(
            day=later_day,
            exercise=self.exercise_one,
            sets="۲",
            reps="۸",
        )
        self.client.force_login(self.client_user)

        response = self.client.get(reverse("register:profile_workout_programs"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'name="session_{old_day.items.first().pk}_main_1_actual_load_kg"',
        )
        self.assertContains(response, "نتیجه واقعی هر ست")

    def test_best_record_and_weekly_completion_are_saved_per_user_and_per_day(self):
        program = WorkoutProgram.objects.create(
            user=self.client_user,
            prescribed_by=self.admin,
            title="برنامه سه هفته‌ای",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 21),
            is_published=True,
        )
        first_day = WorkoutProgramDay.objects.create(program=program, name="روز اول", order=1)
        second_day = WorkoutProgramDay.objects.create(program=program, name="روز دوم", order=2)
        WorkoutProgramExercise.objects.create(
            day=first_day,
            exercise=self.exercise_one,
            sets="۳",
            reps="۱۰",
        )
        WorkoutProgramExercise.objects.create(
            day=second_day,
            exercise=self.exercise_two,
            sets="۳",
            reps="۱۰",
        )
        for phase in ("warmup", "cooldown"):
            WorkoutProgramCorrective.objects.create(
                program=program,
                corrective_exercise=self.corrective,
                phase=phase,
                reps="۳۰ ثانیه",
            )
        other_user = User.objects.create(phone="09129990003", fullname="other_athlete")
        WorkoutBestRecord.objects.create(
            user=other_user,
            exercise=self.exercise_one,
            value=Decimal("98.75"),
        )
        self.client.force_login(self.client_user)
        save_url = reverse("register:profile_workout_program_performance", args=[program.pk])

        response = self.client.post(
            save_url,
            {
                "day_id": str(first_day.pk),
                f"best_record_{self.exercise_one.pk}": "57.25",
                "difficulty": "8",
            },
        )
        self.assertRedirects(response, reverse("register:profile_workout_programs"))
        self.assertEqual(
            WorkoutBestRecord.objects.get(user=self.client_user, exercise=self.exercise_one).value,
            Decimal("57.25"),
        )

        self.client.post(
            save_url,
            {
                "day_id": str(second_day.pk),
                f"best_record_{self.exercise_two.pk}": "40",
                "difficulty": "6",
            },
        )
        for _ in range(4):
            self.client.post(
                save_url,
                {
                    "day_id": str(first_day.pk),
                    f"best_record_{self.exercise_one.pk}": "50",
                    "difficulty": "7",
                },
            )

        self.assertEqual(
            WorkoutProgramDayProgress.objects.get(day=first_day).completed_count,
            3,
        )
        self.assertEqual(
            WorkoutProgramDayProgress.objects.get(day=second_day).completed_count,
            1,
        )
        self.assertEqual(
            WorkoutBestRecord.objects.get(user=self.client_user, exercise=self.exercise_one).value,
            Decimal("57.25"),
        )

        athlete_page = self.client.get(reverse("register:profile_workout_programs"))
        rendered_program = next(
            item for item in athlete_page.context["programs"] if item.pk == program.pk
        )
        self.assertEqual(rendered_program.duration_weeks, 3)
        rendered_days = {day.pk: day for day in rendered_program.days.all()}
        self.assertEqual(rendered_days[first_day.pk].completed_count, 3)
        self.assertEqual(rendered_days[first_day.pk].completion_checks, range(1, 4))
        self.assertEqual(rendered_days[second_day.pk].completed_count, 1)
        self.assertContains(
            athlete_page,
            f'name="session_{first_day.items.first().pk}_main_1_actual_load_kg"',
        )
        self.assertContains(athlete_page, 'step="0.01"')
        self.assertContains(athlete_page, "ثبت جلسه و نتیجه ست‌ها")
        self.assertContains(
            athlete_page,
            f'id="workout-completion-{first_day.pk}-3" type="checkbox" disabled checked',
        )
        rendered_html = athlete_page.content.decode()
        self.assertNotIn('<details class="workout-day" open', rendered_html)
        self.assertLess(
            rendered_html.index('class="workout-corrective workout-corrective--warmup"'),
            rendered_html.index('<details class="workout-day"'),
        )
        self.assertLess(
            rendered_html.index('<details class="workout-day"'),
            rendered_html.index('class="workout-corrective workout-corrective--cooldown"'),
        )
        self.client.force_login(self.admin)
        admin_page = self.client.get(
            reverse("register:admin_user_hub", args=[self.client_user.pk])
        )
        self.assertContains(admin_page, "57.25")
        self.assertNotContains(admin_page, "98.75")

    def test_automatic_program_uses_recent_recovery_and_session_preferences(self):
        section = ExerciseSecondaryMovementType.objects.create(name="هدف تطبیقی سینه")
        ExerciseSetType.objects.create(set_count="4", goal="adaptive-test")
        ExerciseRepetitionType.objects.create(reps="10", goal="adaptive-test")
        ExerciseRestType.objects.create(rest_time="60 ثانیه", goal="adaptive-test")
        self.exercise_one.secondary_movement_type = section
        self.exercise_one.save(update_fields=["secondary_movement_type"])
        profile = AthleteTrainingProfile.objects.create(
            user=self.client_user,
            experience_years=0,
            session_duration_minutes=15,
            preferred_days=[WorkoutProgramDay.Weekday.MONDAY],
        )
        profile.available_equipment.add(self.exercise_one.equipment_type)
        today = timezone.localdate()
        for days_ago in (0, 1):
            DailyMoodEntry.objects.create(
                user=self.client_user,
                entry_date=today - timedelta(days=days_ago),
                anxiety_score=2,
                motivation_score=4,
                sleep_quality_score=3,
                training_satisfaction_score=4,
                training_readiness_score=2,
                fatigue_score=9,
                muscle_soreness_score=9,
            )

        preview = AutomaticProgramService().generate(
            difficulty=self.exercise_one.difficulty_level,
            gender=None,
            abnormalities=[],
            sessions=1,
            movements=1,
            goal="adaptive-test",
            secondary_movement_counts={str(section.pk): 1},
            athlete=self.client_user,
        )

        item = preview["days"][0]["items"][0]
        self.assertTrue(preview["metadata"]["readiness"]["low"])
        self.assertEqual(item["sets"], "3")
        self.assertEqual(item["target_rpe"], 7)
        self.assertEqual(item["target_rir"], 3)
        self.assertIn("آمادگی و خواب", item["selection_reason"])
        self.assertEqual(preview["days"][0]["scheduled_weekday"], WorkoutProgramDay.Weekday.MONDAY)
        self.assertLessEqual(preview["days"][0]["estimated_duration_minutes"], 15)
        self.assertEqual(preview["metadata"]["weekly_volume_target"]["experience_band"], "beginner")
        self.assertEqual(preview["metadata"]["weekly_volume_target"]["minimum"], 4)
        self.assertIn("سینه", preview["metadata"]["weekly_muscle_volume"])

    def test_automatic_program_uses_only_coach_approved_progression_load(self):
        section = ExerciseSecondaryMovementType.objects.create(name="هدف پیشرفت سینه")
        self.exercise_one.secondary_movement_type = section
        self.exercise_one.save(update_fields=["secondary_movement_type"])
        program = WorkoutProgram.objects.create(
            user=self.client_user,
            prescribed_by=self.admin,
            title="برنامه مبنای پیشنهاد",
        )
        session = WorkoutSession.objects.create(user=self.client_user, program=program)
        TrainingAdjustmentRecommendation.objects.create(
            user=self.client_user,
            program=program,
            source_session=session,
            exercise=self.exercise_one,
            movement_name=self.exercise_one.name,
            recommendation_type=TrainingAdjustmentRecommendation.RecommendationType.INCREASE_LOAD,
            confidence=TrainingAdjustmentRecommendation.Confidence.HIGH,
            suggested_load_kg=Decimal("45"),
            reviewed_load_kg=Decimal("42.5"),
            rationale="تکرار هدف با RIR مناسب تکمیل شد.",
            status=TrainingAdjustmentRecommendation.Status.ACCEPTED,
            reviewed_by=self.admin,
            reviewed_at=timezone.now(),
        )
        rejected_session = WorkoutSession.objects.create(user=self.client_user, program=program)
        TrainingAdjustmentRecommendation.objects.create(
            user=self.client_user,
            program=program,
            source_session=rejected_session,
            exercise=self.exercise_two,
            movement_name=self.exercise_two.name,
            recommendation_type=TrainingAdjustmentRecommendation.RecommendationType.INCREASE_LOAD,
            confidence=TrainingAdjustmentRecommendation.Confidence.HIGH,
            suggested_load_kg=Decimal("50"),
            reviewed_load_kg=Decimal("49"),
            rationale="این پیشنهاد رد شده است.",
            status=TrainingAdjustmentRecommendation.Status.REJECTED,
            reviewed_by=self.admin,
            reviewed_at=timezone.now(),
        )

        preview = AutomaticProgramService().generate(
            difficulty=self.exercise_one.difficulty_level,
            gender=None,
            abnormalities=[],
            sessions=1,
            movements=1,
            goal="general",
            secondary_movement_counts={str(section.pk): 1},
            athlete=self.client_user,
        )

        item = preview["days"][0]["items"][0]
        self.assertEqual(Decimal(item["target_load_kg"]), Decimal("42.50"))
        self.assertEqual([row["percent"] for row in item["warmup_sets"]], [50, 70, 85])
        self.assertIn("پیشنهاد پیشرفتِ تأییدشده", item["selection_reason"])
        self.assertEqual(preview["metadata"]["progression_recommendations_applied"], 1)
        self.assertNotIn(self.exercise_two.pk, preview["metadata"]["selected_exercise_ids"])

    def test_deload_sets_are_exposed_and_enforced_when_recording_session(self):
        program = WorkoutProgram.objects.create(
            user=self.client_user,
            prescribed_by=self.admin,
            title="چرخه چهار هفته‌ای",
            start_date=timezone.localdate() - timedelta(days=21),
            is_published=True,
            generation_metadata={
                "periodization": {
                    "cycle_weeks": 4,
                    "deload_week": 4,
                    "deload_sets_multiplier": 0.7,
                }
            },
        )
        day = WorkoutProgramDay.objects.create(program=program, name="روز سبک", order=1)
        item = WorkoutProgramExercise.objects.create(
            day=day, exercise=self.exercise_one, sets="4", reps="10", rest="60 ثانیه"
        )
        self.assertTrue(TrainingPeriodizationService.current_week(program)["is_deload"])
        self.assertEqual(TrainingPeriodizationService.active_sets(program, item.sets), "2")

        self.client.force_login(self.client_user)
        response = self.client.get(reverse("register:profile_workout_programs"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "هفتهٔ سبک")
        self.assertContains(response, f'name="session_{item.pk}_main_2_actual_reps"')
        self.assertNotContains(response, f'name="session_{item.pk}_main_3_actual_reps"')

        with self.assertRaises(ValidationError) as error:
            TrainingDataService.create_session(
                user=self.client_user,
                program=program,
                day=day,
                payload={
                    "sets": [
                        {"program_exercise_id": item.pk, "slot": "main", "set_number": 3, "actual_reps": 10}
                    ]
                },
            )
        self.assertIn("sets", error.exception.message_dict)

    def _enhancement_session(self, *, program, day, item, started_at, load, reps=10, rir=2, warmup=False):
        session = WorkoutSession.objects.create(
            user=self.client_user,
            program=program,
            program_day=day,
            started_at=started_at,
            status=WorkoutSession.Status.COMPLETED,
        )
        WorkoutSetResult.objects.create(
            session=session,
            program_exercise=item,
            exercise=self.exercise_one,
            slot=WorkoutSetResult.Slot.MAIN,
            set_number=1,
            planned_reps="8-12",
            planned_reps_min=8,
            planned_reps_max=12,
            planned_sets_min=2,
            planned_sets_max=3,
            actual_reps=reps,
            actual_load_kg=Decimal(str(load)),
            rir=rir,
            is_warmup=warmup,
        )
        return session

    def test_e1rm_ignores_warmups_and_creates_reviewed_deload_after_sustained_drop(self):
        from account.services.exercise_strength_service import ExerciseStrengthService

        program = WorkoutProgram.objects.create(
            user=self.client_user, prescribed_by=self.admin, title="افت عملکرد", is_published=False
        )
        day = WorkoutProgramDay.objects.create(program=program, name="روز", order=1)
        item = WorkoutProgramExercise.objects.create(
            day=day, exercise=self.exercise_one, sets="3", reps="8-12", rest="90 ثانیه"
        )
        now = timezone.now()
        sessions = []
        for index, load in enumerate((100, 96, 92)):
            session = self._enhancement_session(
                program=program,
                day=day,
                item=item,
                started_at=now - timedelta(days=3 - index),
                load=load,
            )
            WorkoutSetResult.objects.create(
                session=session,
                program_exercise=item,
                exercise=self.exercise_one,
                slot=WorkoutSetResult.Slot.MAIN,
                set_number=1,
                planned_reps="warm-up",
                actual_reps=5,
                actual_load_kg=Decimal("250"),
                is_warmup=True,
            )
            ExerciseStrengthService.record_session(session)
            sessions.append(session)

        self.assertEqual(ExerciseStrengthEstimate.objects.filter(user=self.client_user).count(), 3)
        self.assertFalse(ExerciseStrengthEstimate.objects.filter(source_set__is_warmup=True).exists())
        recommendation = TrainingAdjustmentRecommendation.objects.get(
            source_session=sessions[-1],
            exercise=self.exercise_one,
            recommendation_type=TrainingAdjustmentRecommendation.RecommendationType.DELOAD,
        )
        self.assertEqual(recommendation.evidence["session_ids"], [sessions[-1].pk, sessions[-2].pk, sessions[-3].pk])
        self.assertEqual(recommendation.evidence["set_multiplier"], "0.5")

    def test_large_equipment_increment_uses_reviewed_reps_first_step(self):
        from account.services.progression_recommendation_service import ProgressionRecommendationService

        section = ExerciseSecondaryMovementType.objects.create(name="تکرار پیش از بار")
        self.exercise_one.secondary_movement_type = section
        self.exercise_one.equipment_type.load_increment_kg = Decimal("5")
        self.exercise_one.equipment_type.save(update_fields=["load_increment_kg"])
        self.exercise_one.save(update_fields=["secondary_movement_type"])
        program = WorkoutProgram.objects.create(
            user=self.client_user, prescribed_by=self.admin, title="بار بزرگ", is_published=False
        )
        day = WorkoutProgramDay.objects.create(program=program, name="روز", order=1)
        item = WorkoutProgramExercise.objects.create(
            day=day, exercise=self.exercise_one, sets="3", reps="8-12", rest="90 ثانیه"
        )
        now = timezone.now()
        older = self._enhancement_session(
            program=program, day=day, item=item, started_at=now - timedelta(days=7), load=20, reps=12
        )
        current = self._enhancement_session(
            program=program, day=day, item=item, started_at=now, load=20, reps=12
        )
        recommendation = ProgressionRecommendationService.create_for_session(current)[0]
        self.assertEqual(recommendation.recommendation_type, TrainingAdjustmentRecommendation.RecommendationType.HOLD)
        self.assertEqual(recommendation.evidence["progression_strategy"], "add_reps_before_load")
        self.assertEqual(recommendation.evidence["next_rep_target"], 13)

        TrainingRecommendationReviewService.review(
            staff_user=self.admin,
            recommendation_id=recommendation.pk,
            status=TrainingAdjustmentRecommendation.Status.ACCEPTED,
            reviewed_load=None,
            coach_note="",
        )
        preview = AutomaticProgramService().generate(
            difficulty=self.exercise_one.difficulty_level,
            gender=None,
            abnormalities=[],
            sessions=1,
            movements=1,
            goal="general",
            secondary_movement_counts={str(section.pk): 1},
            athlete=self.client_user,
        )
        prescribed_item = preview["days"][0]["items"][0]
        self.assertEqual(prescribed_item["exercise"], self.exercise_one.pk)
        self.assertEqual(prescribed_item["reps_min"], 8)
        self.assertEqual(prescribed_item["reps_max"], 13)
        self.assertIn("بار فعلی را حفظ کنید", prescribed_item["note"])
        self.assertIn(recommendation.pk, preview["metadata"]["progression_recommendation_ids"])

    def test_per_muscle_landmarks_periodize_volume_and_preferences_rank_exercises(self):
        from account.models import AthleteExercisePreference, MuscleVolumeLandmark, TrainingGoal

        from account.services.automatic_program_service import AutomaticProgramService
        from account.services.exercise_preference_service import ExercisePreferenceService

        profile = AthleteTrainingProfile.objects.create(
            user=self.client_user,
            primary_goal=TrainingGoal.HYPERTROPHY,
            experience_years=2,
        )
        MuscleVolumeLandmark.objects.update_or_create(
            muscle=self.exercise_one.primary_muscle,
            training_goal=TrainingGoal.HYPERTROPHY,
            experience_band=MuscleVolumeLandmark.ExperienceBand.INTERMEDIATE,
            defaults={"mev": 8, "mav": 12, "mrv": 16},
        )
        target = AutomaticProgramService._weekly_volume_target(
            "volume", profile, muscle=self.exercise_one.primary_muscle, training_goal=TrainingGoal.HYPERTROPHY
        )
        self.assertEqual((target["minimum"], target["optimum"], target["maximum"], target["source"]), (8, 12, 16, "library"))

        days = [{"items": [{"exercise": self.exercise_one.pk, "sets": "2"}]}]
        current_volume = {self.exercise_one.primary_muscle.name: {"weighted_total_sets": [2, 2]}}
        AutomaticProgramService._apply_weekly_volume_progression(
            days,
            {self.exercise_one.pk: self.exercise_one},
            current_volume,
            {self.exercise_one.primary_muscle.name: {"optimum": 6, "maximum": 8}},
            cycle_weeks=4,
        )
        targets = days[0]["items"][0]["weekly_set_targets"]
        self.assertEqual([targets[str(week)] for week in range(1, 5)], ["2", "4", "6", "3"])
        minimum_days = [{"items": [{"exercise": self.exercise_one.pk, "sets": "2"}]}]
        AutomaticProgramService._apply_weekly_volume_progression(
            minimum_days,
            {self.exercise_one.pk: self.exercise_one},
            current_volume,
            {self.exercise_one.primary_muscle.name: {"minimum": 4, "optimum": 6, "maximum": 8}},
            cycle_weeks=4,
        )
        minimum_targets = minimum_days[0]["items"][0]["weekly_set_targets"]
        self.assertEqual([minimum_targets[str(week)] for week in range(1, 5)], ["4", "5", "6", "3"])

        selected = AutomaticProgramService._pick_varied_exercise(
            preferred_pool=[self.exercise_one, self.exercise_two],
            pool=[self.exercise_one, self.exercise_two],
            exposure_counts={},
            session_used_ids=set(),
            cursor=0,
            seed_offset=0,
            favorite_ids={self.exercise_two.pk},
        )
        self.assertEqual(selected.pk, self.exercise_two.pk)
        ExercisePreferenceService.record_program_edits(
            athlete=self.client_user,
            actor=self.admin,
            days_payload=[{"items": [{"generated_exercise": self.exercise_one.pk, "exercise": self.exercise_two.pk}]}],
        )
        preference = AthleteExercisePreference.objects.get(user=self.client_user, exercise=self.exercise_one)
        self.assertEqual(preference.status, AthleteExercisePreference.Status.AVOID)

    def test_low_adherence_reduces_generated_sessions_and_shared_templates_are_scoped(self):
        from account.models import ProgramTemplate, TrainingGoal
        from account.services.program_template_service import ProgramTemplateService
        from account.services.training_adherence_service import TrainingAdherenceService

        section = ExerciseSecondaryMovementType.objects.create(name="قالب قابل اجرا")
        self.exercise_one.secondary_movement_type = section
        self.exercise_one.save(update_fields=["secondary_movement_type"])
        active = WorkoutProgram.objects.create(
            user=self.client_user,
            prescribed_by=self.admin,
            title="نسخه فعال",
            start_date=timezone.localdate(),
            is_published=True,
        )
        for order in range(1, 4):
            WorkoutProgramDay.objects.create(program=active, name=f"روز {order}", order=order)
        adherence = TrainingAdherenceService.snapshot(self.client_user)
        self.assertEqual(adherence["percent"], 0)
        self.assertTrue(adherence["low"])
        self.assertEqual(TrainingAdherenceService.effective_sessions(3, adherence), 1)

        preview = AutomaticProgramService().generate(
            difficulty=self.exercise_one.difficulty_level,
            gender=None,
            abnormalities=[],
            sessions=3,
            movements=1,
            goal="general",
            secondary_movement_counts={str(section.pk): 1},
            athlete=self.client_user,
        )
        self.assertEqual(len(preview["days"]), 1)
        self.assertEqual(preview["metadata"]["adherence"]["effective_sessions"], 1)

        coach = User.objects.create_user(
            phone="09129990009", fullname="enabled_coach", password="StrongPass123",
            is_coach=True, can_use_automatic_programming=True,
        )
        template = ProgramTemplate.objects.create(
            owner=self.admin,
            title="قالب عمومی",
            training_goal=TrainingGoal.GENERAL_FITNESS,
            difficulty=self.exercise_one.difficulty_level,
            days_per_week=2,
            weeks=6,
            is_shared_with_enabled_coaches=True,
        )
        self.assertTrue(ProgramTemplateService.accessible_to(coach).filter(pk=template.pk).exists())
        self.assertFalse(ProgramTemplateService.accessible_to(self.client_user).filter(pk=template.pk).exists())
        template_preview = AutomaticProgramService().generate(
            difficulty=self.exercise_one.difficulty_level,
            gender=None,
            abnormalities=[],
            sessions=1,
            movements=1,
            goal="general",
            secondary_movement_counts={str(section.pk): 1},
            athlete=self.client_user,
            template=template,
        )
        self.assertEqual(template_preview["metadata"]["periodization"]["cycle_weeks"], 6)
        self.assertEqual(template_preview["metadata"]["periodization"]["deload_week"], 6)
        one_week_program = WorkoutProgram.objects.create(
            user=self.client_user,
            title="چرخه تک هفته",
            generation_metadata={
                "periodization": {"cycle_weeks": 1, "deload_week": None, "deload_sets_multiplier": 0.5}
            },
        )
        self.assertFalse(TrainingPeriodizationService.current_week(one_week_program)["is_deload"])
