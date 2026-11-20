"""Role-scoped coverage for the mobile app's athlete, coach and admin screens."""

from __future__ import annotations

import io
import tempfile
from datetime import datetime, time

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from account.models import (
    BodyCircumferenceMeasurement,
    CaliperMeasurement,
    CardioExercise,
    CoachRequest,
    Notification,
    User,
    WorkoutProgram,
)
from api.auth import issue_token_pair
from api.tests_shared import ApiTestCase
from home.models import ArticleBlogModel, SeriesModel


def _png_upload(name: str = "cover.png") -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (21, 94, 82)).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


class ManagementApiTests(ApiTestCase):
    """Every request the privileged mobile screens issue must keep working."""

    def create_user(self, fullname: str, phone: str, **extra) -> User:
        return User.objects.create_user(
            fullname=fullname,
            phone=phone,
            password="StrongPass123",
            **extra,
        )

    def headers_for(self, user: User) -> dict:
        tokens = issue_token_pair(user=user, name="management-tests")
        return {"HTTP_AUTHORIZATION": f"Bearer {tokens['access_token']}"}

    def test_dashboard_counts_only_published_own_programs(self):
        WorkoutProgram.objects.create(user=self.user, title="منتشر شده", is_published=True)
        WorkoutProgram.objects.create(user=self.user, title="پیش‌نویس", is_published=False)
        WorkoutProgram.objects.create(user=self.other_user, title="دیگری", is_published=True)

        response = self.client.get(reverse("api:dashboard"), **self.headers)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["counts"]["programs"], 1)

    def test_program_payload_exposes_mobile_screen_fields(self):
        WorkoutProgram.objects.create(
            user=self.user,
            title="برنامه من",
            is_published=True,
            program_type="full_body",
            requires_payment=True,
            price=150000,
        )

        response = self.client.get(reverse("api:programs"), **self.headers)

        self.assertEqual(response.status_code, 200)
        item = response.json()["data"]["items"][0]
        self.assertEqual(item["program_type"], "full_body")
        self.assertTrue(item["is_published"])
        self.assertTrue(item["requires_payment"])
        self.assertEqual(item["price"], 150000)
        self.assertEqual(item["correctives"], [])

    def test_notifications_expose_id_and_key_for_mark_read(self):
        notification = Notification.objects.create(user=self.user, title="خبر", message="متن")

        response = self.client.get(reverse("api:notifications"), **self.headers)

        self.assertEqual(response.status_code, 200)
        item = next(
            entry for entry in response.json()["data"]["items"] if entry["key"] == f"notification:{notification.pk}"
        )
        self.assertEqual(item["id"], notification.pk)

        allowed = self.client.post(
            reverse("api:notification_read", kwargs={"pk": notification.pk}),
            **self.headers,
        )
        self.assertEqual(allowed.status_code, 200)
        notification.refresh_from_db()
        self.assertTrue(notification.read)

    def test_admin_user_filters_split_roles(self):
        admin = self.create_user("مدیر", "09120000501", is_admin=True)
        coach = self.create_user("مربی", "09120000502", is_coach=True)
        headers = self.headers_for(admin)

        coaches = self.client.get(reverse("api:admin_users") + "?role=coach", **headers).json()["data"]["items"]
        self.assertEqual({row["id"] for row in coaches}, {coach.pk})

        athletes = self.client.get(reverse("api:admin_users") + "?role=user", **headers).json()["data"]["items"]
        self.assertIn(self.user.pk, {row["id"] for row in athletes})
        self.assertNotIn(coach.pk, {row["id"] for row in athletes})

    def test_admin_can_grant_coach_role_and_assign_coach(self):
        admin = self.create_user("مدیر دو", "09120000511", is_admin=True)
        headers = self.headers_for(admin)
        coach = self.create_user("مربی دو", "09120000512")
        athlete = self.create_user("ورزشکار دو", "09120000513")

        role = self.client.patch(
            reverse("api:admin_coach_role", kwargs={"user_id": coach.pk}),
            data='{"is_coach": true}',
            content_type="application/json",
            **headers,
        )
        self.assertEqual(role.status_code, 200)
        coach.refresh_from_db()
        self.assertTrue(coach.is_coach)

        assigned = self.client.patch(
            reverse("api:admin_assign_coach", kwargs={"user_id": athlete.pk}),
            data=f'{{"coach_id": {coach.pk}}}',
            content_type="application/json",
            **headers,
        )
        self.assertEqual(assigned.status_code, 200)
        athlete.refresh_from_db()
        self.assertEqual(athlete.coach_id, coach.pk)

    def test_dual_role_account_can_manage_a_roster(self):
        """An administrator may also carry coach access (the owner account does)."""
        actor = self.create_user("مدیر ارشد", "09120000514", is_admin=True)
        owner = self.create_user("مالک", "09120000519", is_admin=True)
        athlete = self.create_user("شاگرد مالک", "09120000515", coach=owner)

        grant = self.client.patch(
            reverse("api:admin_coach_role", kwargs={"user_id": owner.pk}),
            data='{"is_coach": true}',
            content_type="application/json",
            **self.headers_for(actor),
        )
        self.assertEqual(grant.status_code, 200)
        owner.refresh_from_db()
        self.assertTrue(owner.is_coach)

        # An admin cannot change their own coach role, even to grant it.
        self_change = self.client.patch(
            reverse("api:admin_coach_role", kwargs={"user_id": actor.pk}),
            data='{"is_coach": true}',
            content_type="application/json",
            **self.headers_for(actor),
        )
        self.assertEqual(self_change.status_code, 409)

        roster = self.client.get(reverse("api:coach_athletes"), **self.headers_for(owner))
        self.assertEqual(roster.status_code, 200)
        self.assertEqual({row["id"] for row in roster.json()["data"]["items"]}, {athlete.pk})

    def test_coach_roster_supports_search(self):
        coach = self.create_user("مربی جستجو", "09120000516", is_coach=True)
        headers = self.headers_for(coach)
        match = self.create_user("سارا محمدی", "09120000517", coach=coach)
        self.create_user("رضا کریمی", "09120000518", coach=coach)

        response = self.client.get(reverse("api:coach_athletes") + "?q=سارا", **headers)

        self.assertEqual(response.status_code, 200)
        self.assertEqual({row["id"] for row in response.json()["data"]["items"]}, {match.pk})

    def test_non_admin_cannot_open_admin_routes(self):
        response = self.client.get(reverse("api:admin_users"), **self.headers)
        self.assertEqual(response.status_code, 403)

    def test_automatic_programming_api_requires_admin_or_enabled_coach(self):
        coach = self.create_user("مربی بدون دسترسی", "09120000590", is_coach=True)
        athlete = self.user
        athlete.coach = coach
        athlete.save(update_fields=["coach"])
        payload = {"action": "preview", "user": athlete.pk}

        denied = self.client.post(
            reverse("api:admin_automatic_program"),
            data=payload,
            content_type="application/json",
            **self.headers_for(coach),
        )
        self.assertEqual(denied.status_code, 403)

        coach.can_use_automatic_programming = True
        coach.save(update_fields=["can_use_automatic_programming"])
        allowed_for_roster = self.client.post(
            reverse("api:admin_automatic_program"),
            data=payload,
            content_type="application/json",
            **self.headers_for(coach),
        )
        self.assertNotEqual(allowed_for_roster.status_code, 403)

        outside_roster = self.client.post(
            reverse("api:admin_automatic_program"),
            data={"action": "preview", "user": self.other_user.pk},
            content_type="application/json",
            **self.headers_for(coach),
        )
        self.assertEqual(outside_roster.status_code, 404)

    def test_program_template_api_lists_only_templates_the_coach_can_use(self):
        from account.models import ExerciseDifficultyLevel, ProgramTemplate, TrainingGoal

        difficulty, _ = ExerciseDifficultyLevel.objects.get_or_create(name="سطح قالب API")
        coach = self.create_user(
            "مربی قالب API", "09120000591", is_coach=True, can_use_automatic_programming=True
        )
        private_owner = self.create_user("مالک خصوصی", "09120000592", is_admin=True)
        shared = ProgramTemplate.objects.create(
            owner=private_owner,
            title="قالب مشترک",
            training_goal=TrainingGoal.GENERAL_FITNESS,
            difficulty=difficulty,
            days_per_week=3,
            weeks=8,
            is_shared_with_enabled_coaches=True,
        )
        private = ProgramTemplate.objects.create(
            owner=private_owner,
            title="قالب خصوصی",
            training_goal=TrainingGoal.STRENGTH,
            difficulty=difficulty,
            days_per_week=4,
            weeks=12,
        )

        response = self.client.get(
            reverse("api:admin_program_templates") + "?q=مشترک",
            **self.headers_for(coach),
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            [row["id"] for row in response.json()["data"]["items"]],
            [shared.pk],
        )
        self.assertEqual(response.json()["data"]["items"][0]["weeks"], 8)

        forbidden = self.client.get(
            reverse("api:admin_program_templates"),
            **self.headers,
        )
        self.assertEqual(forbidden.status_code, 403)
        admin_response = self.client.get(
            reverse("api:admin_program_templates"),
            **self.headers_for(private_owner),
        )
        self.assertEqual(admin_response.status_code, 200, admin_response.content)
        self.assertEqual(
            {row["id"] for row in admin_response.json()["data"]["items"]},
            {shared.pk, private.pk},
        )

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_admin_course_lifecycle_matches_admin_content_screen(self):
        admin = self.create_user("مدیر محتوا", "09120000521", is_admin=True)
        headers = self.headers_for(admin)

        created = self.client.post(
            reverse("api:admin_courses"),
            data={
                "title": "دوره آزمایشی",
                "description": "توضیح",
                "introduction": "معرفی",
                "free": "true",
                "image": _png_upload(),
            },
            **headers,
        )
        self.assertEqual(created.status_code, 201)
        course_id = created.json()["data"]["id"]

        detail = self.client.get(reverse("api:admin_course_detail", kwargs={"course_id": course_id}), **headers)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["data"]["seasons"], [])

        season = self.client.post(
            reverse("api:admin_course_seasons", kwargs={"course_id": course_id}),
            data='{"number": 1, "title": "فصل اول"}',
            content_type="application/json",
            **headers,
        )
        self.assertEqual(season.status_code, 201)
        season_id = season.json()["data"]["id"]

        episode = self.client.post(
            reverse("api:admin_season_episodes", kwargs={"season_id": season_id}),
            data={"title": "قسمت اول"},
            **headers,
        )
        self.assertEqual(episode.status_code, 201)

        reloaded = self.client.get(reverse("api:admin_course_detail", kwargs={"course_id": course_id}), **headers)
        seasons = reloaded.json()["data"]["seasons"]
        self.assertEqual(seasons[0]["title"], "فصل اول")
        self.assertEqual(seasons[0]["episodes"][0]["title"], "قسمت اول")

        unpublished = self.client.patch(
            reverse("api:admin_course_detail", kwargs={"course_id": course_id}),
            data='{"is_published": false}',
            content_type="application/json",
            **headers,
        )
        self.assertEqual(unpublished.status_code, 200)
        self.assertFalse(unpublished.json()["data"]["is_published"])

        listing = self.client.get(reverse("api:admin_courses") + "?page_size=60", **headers)
        self.assertIn(course_id, {row["id"] for row in listing.json()["data"]["items"]})

    def test_admin_transactions_return_all_ledgers(self):
        admin = self.create_user("مدیر مالی", "09120000531", is_admin=True)
        headers = self.headers_for(admin)

        response = self.client.get(reverse("api:admin_transactions"), **headers)

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(
            set(data),
            {"orders", "document_payments", "workout_program_payments"},
        )
        self.assertIn("items", data["orders"])

    def test_coach_only_reaches_assigned_athletes(self):
        coach = self.create_user("مربی سه", "09120000541", is_coach=True)
        athlete = self.create_user("شاگرد", "09120000542", coach=coach)
        stranger = self.create_user("غریبه", "09120000543")
        headers = self.headers_for(coach)

        roster = self.client.get(reverse("api:coach_athletes"), **headers)
        self.assertEqual(roster.status_code, 200)
        self.assertEqual({row["id"] for row in roster.json()["data"]["items"]}, {athlete.pk})

        analysis = self.client.get(
            reverse("api:coach_athlete_analysis", kwargs={"athlete_id": athlete.pk}), **headers
        )
        self.assertEqual(analysis.status_code, 200)
        self.assertEqual(analysis.json()["data"]["user"]["id"], athlete.pk)

        foreign = self.client.get(
            reverse("api:coach_athlete_analysis", kwargs={"athlete_id": stranger.pk}), **headers
        )
        self.assertEqual(foreign.status_code, 404)

        programs = self.client.get(
            reverse("api:coach_athlete_programs", kwargs={"athlete_id": athlete.pk}), **headers
        )
        self.assertEqual(programs.status_code, 200)
        self.assertIn("items", programs.json()["data"])

    def test_analysis_date_range_filters_measurements_per_role(self):
        """Coaches/admin/athletes can narrow analysis history to a Jalali window."""
        import jdatetime

        coach = self.create_user("مربی بازه", "09120000551", is_coach=True)
        admin = self.create_user("مدیر بازه", "09120000552", is_admin=True)
        athlete = self.create_user("شاگرد بازه", "09120000553", coach=coach)

        inside = jdatetime.date(1405, 3, 10).togregorian()
        outside = jdatetime.date(1404, 1, 5).togregorian()
        for moment in (inside, outside):
            measured_at = timezone.make_aware(datetime.combine(moment, time(12, 0)))
            BodyCircumferenceMeasurement.objects.create(
                user=athlete,
                waist_cm="80",
                measured_at=measured_at,
            )
            CaliperMeasurement.objects.create(
                user=athlete,
                abdominal_mm="20",
                measured_at=measured_at,
            )

        endpoints = [
            ("api:analysis_me", athlete, {}),
            ("api:coach_athlete_analysis", coach, {"athlete_id": athlete.pk}),
            ("api:admin_user_analysis", admin, {"user_id": athlete.pk}),
        ]
        for name, actor, kwargs in endpoints:
            headers = self.headers_for(actor)
            full = self.client.get(reverse(name, kwargs=kwargs), **headers)
            self.assertEqual(full.status_code, 200)
            self.assertEqual(len(full.json()["data"]["circumference_measurements"]), 2)

            ranged = self.client.get(
                reverse(name, kwargs=kwargs) + "?start=1405/01/01&end=1405/12/29", **headers
            )
            self.assertEqual(ranged.status_code, 200)
            payload = ranged.json()["data"]
            self.assertEqual(payload["range"], {"start": "1405/01/01", "end": "1405/12/29"})
            self.assertEqual(len(payload["circumference_measurements"]), 1)
            self.assertEqual(len(payload["caliper_measurements"]), 1)

            invalid = self.client.get(reverse(name, kwargs=kwargs) + "?start=nonsense", **headers)
            self.assertEqual(invalid.status_code, 200)
            self.assertEqual(invalid.json()["data"]["range"], {"start": None, "end": None})

    def test_coach_can_record_measurements_for_own_athlete_only(self):
        """Requirement 3c: a coach records circumference, caliper and health data."""
        coach = self.create_user("مربی اندازه", "09120000571", is_coach=True)
        other_coach = self.create_user("مربی دیگر", "09120000572", is_coach=True)
        athlete = self.create_user("شاگرد اندازه", "09120000573", coach=coach)
        foreign = self.create_user("شاگرد غریبه", "09120000574", coach=other_coach)
        headers = self.headers_for(coach)

        circumference = self.client.post(
            reverse("api:coach_athlete_measurements", kwargs={"athlete_id": athlete.pk, "kind": "circumference"}),
            data={"weight_kg": "82.5", "waist_cm": "88", "date": "1404/10/12"},
            content_type="application/json",
            **headers,
        )
        self.assertEqual(circumference.status_code, 201)
        row = BodyCircumferenceMeasurement.objects.get(pk=circumference.json()["data"]["measurement"]["id"])
        self.assertEqual(row.user_id, athlete.pk)
        self.assertEqual(row.measured_at_jalali, "1404/10/12")
        athlete.refresh_from_db()
        self.assertEqual(str(athlete.weight_kg), "82.5")

        caliper = self.client.post(
            reverse("api:coach_athlete_measurements", kwargs={"athlete_id": athlete.pk, "kind": "caliper"}),
            data={"abdominal_mm": "22"},
            content_type="application/json",
            **headers,
        )
        self.assertEqual(caliper.status_code, 201)
        self.assertTrue(
            CaliperMeasurement.objects.filter(
                pk=caliper.json()["data"]["measurement"]["id"], user=athlete
            ).exists()
        )

        health = self.client.post(
            reverse("api:coach_athlete_measurements", kwargs={"athlete_id": athlete.pk, "kind": "health"}),
            data={
                "category": "injury",
                "body_region": "leg",
                "severity": 4,
                "training_restriction": True,
                "training_restriction_notes": "بدون دویدن تا پیگیری بعدی",
                "date": "1404/10/12",
            },
            content_type="application/json",
            **headers,
        )
        self.assertEqual(health.status_code, 201)

        # Another coach's athlete is invisible even with the write endpoint.
        denied = self.client.post(
            reverse("api:coach_athlete_measurements", kwargs={"athlete_id": foreign.pk, "kind": "circumference"}),
            data={"waist_cm": "70"},
            content_type="application/json",
            **headers,
        )
        self.assertEqual(denied.status_code, 404)

    def test_coach_blog_submission_requires_admin_approval(self):
        """A coach drafts a post; it only becomes public after admin approval."""
        coach = self.create_user("مربی وبلاگ", "09120000561", is_coach=True)
        admin = self.create_user("مدیر وبلاگ", "09120000562", is_admin=True)

        created = self.client.post(
            reverse("api:coach_articles"),
            data={
                "title": "مقاله مربی",
                "reading_time": "4",
                "article_excerpt": "خلاصه",
                "article_description": "متن کامل",
                "image": _png_upload(),
            },
            **self.headers_for(coach),
        )
        self.assertEqual(created.status_code, 201)
        article_id = created.json()["data"]["article"]["id"]
        self.assertEqual(created.json()["data"]["article"]["publication_status"], "pending")

        pending = ArticleBlogModel.objects.get(pk=article_id)
        self.assertEqual(pending.author_id, coach.pk)
        self.assertEqual(pending.publication_status, ArticleBlogModel.PublicationStatus.PENDING)

        # Pending posts stay out of the public listing and detail endpoints.
        public_items = self.client.get(reverse("api:article_list")).json()["data"]["items"]
        self.assertNotIn(article_id, [item["id"] for item in public_items])
        self.assertEqual(
            self.client.get(reverse("api:article_detail", kwargs={"slug": pending.slug})).status_code,
            404,
        )

        # The moderation queue surfaces it to administrators only.
        queue = self.client.get(reverse("api:admin_articles") + "?status=pending", **self.headers_for(admin))
        self.assertEqual(queue.status_code, 200)
        self.assertIn(article_id, [row["id"] for row in queue.json()["data"]["items"]])
        self.assertEqual(
            self.client.get(reverse("api:admin_articles"), **self.headers_for(coach)).status_code,
            403,
        )

        approved = self.patch_json(
            "api:admin_article_moderate",
            {"publication_status": "approved"},
            url_kwargs={"article_id": article_id},
            **self.headers_for(admin),
        )
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(approved.json()["data"]["article"]["publication_status"], "approved")

        public_items = self.client.get(reverse("api:article_list")).json()["data"]["items"]
        self.assertIn(article_id, [item["id"] for item in public_items])

        # Athletes cannot submit posts at all.
        denied = self.client.post(
            reverse("api:coach_articles"),
            data={"title": "بدون اجازه", "reading_time": "1", "image": _png_upload()},
            **self.headers,
        )
        self.assertEqual(denied.status_code, 403)

        self.patch_json(
            "api:admin_article_moderate",
            {"publication_status": "rejected"},
            url_kwargs={"article_id": article_id},
            **self.headers_for(admin),
        )
        article = ArticleBlogModel.objects.get(pk=article_id)
        self.assertEqual(article.publication_status, ArticleBlogModel.PublicationStatus.REJECTED)
        self.assertEqual(
            self.client.get(reverse("api:article_detail", kwargs={"slug": article.slug})).status_code,
            404,
        )

    def test_coach_handles_only_own_athlete_requests(self):
        coach = self.create_user("مربی درخواست", "09120000601", is_coach=True)
        athlete = self.create_user("شاگرد درخواست", "09120000602", coach=coach)
        other_coach = self.create_user("مربی دیگر", "09120000603", is_coach=True)
        other_athlete = self.create_user("شاگرد دیگر", "09120000604", coach=other_coach)
        own_request = CoachRequest.objects.create(
            user=athlete,
            sessions_per_week=CoachRequest.SessionsPerWeek.THREE,
            wants_workout=True,
        )
        foreign_request = CoachRequest.objects.create(
            user=other_athlete,
            sessions_per_week=CoachRequest.SessionsPerWeek.TWO,
            wants_diet=True,
        )

        response = self.client.get(
            reverse("api:coach_athlete_requests"), **self.headers_for(coach)
        )
        self.assertEqual(response.status_code, 200)
        ids = [row["id"] for row in response.json()["data"]["items"]]
        self.assertEqual(ids, [own_request.pk])

        handled = self.patch_json(
            "api:coach_athlete_request_handled",
            {"status": "handled"},
            url_kwargs={"request_id": own_request.pk},
            **self.headers_for(coach),
        )
        self.assertEqual(handled.status_code, 200)
        own_request.refresh_from_db()
        self.assertEqual(own_request.status, CoachRequest.Status.HANDLED)

        denied = self.patch_json(
            "api:coach_athlete_request_handled",
            {"status": "handled"},
            url_kwargs={"request_id": foreign_request.pk},
            **self.headers_for(coach),
        )
        self.assertEqual(denied.status_code, 404)

        athlete_denied = self.client.get(
            reverse("api:coach_athlete_requests"), **self.headers
        )
        self.assertEqual(athlete_denied.status_code, 403)

    def test_cardio_exercises_are_public_library_resources(self):
        exercise = CardioExercise.objects.create(
            name="تردمیل",
            name_en="Treadmill",
            performance_type="interval",
        )

        listing = self.client.get(reverse("api:library:cardio_exercise_list"))
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()["data"]["items"][0]["id"], exercise.pk)

        detail = self.client.get(reverse("api:library:cardio_exercise_detail", kwargs={"pk": exercise.pk}))
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["data"]["name_en"], "Treadmill")

        catalog = self.client.get(reverse("api:library:catalog")).json()["data"]["resources"]
        self.assertIn("cardio-exercises", {resource["key"] for resource in catalog})

    def test_api_cart_persists_between_bearer_requests(self):
        course = SeriesModel.objects.create(
            title="دوره سبد خرید",
            image="series_courses/cart-test.png",
            author=self.other_user,
            free=False,
            main_price="100000",
            is_published=True,
        )

        added = self.client.post(reverse("api:cart_add", kwargs={"pk": course.pk}), **self.headers)
        self.assertEqual(added.status_code, 200)

        listing = self.client.get(reverse("api:cart_detail"), **self.headers)
        product_ids = [item["product"]["id"] for item in listing.json()["data"]["items"]]
        self.assertIn(course.pk, product_ids)

        removed = self.client.delete(reverse("api:cart_delete", kwargs={"pk": course.pk}), **self.headers)
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(removed.json()["data"]["items"], [])
