"""Three-role boundaries: admin, coach and athlete."""

from __future__ import annotations

import io

from PIL import Image
from django.test import TestCase
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile

from account.models import (
    CoachApplication,
    CoachRequest,
    ExerciseSuggestion,
    Notification,
    User,
)
from home.models import ArticleBlogModel
from api.auth import issue_token_pair
from api.tests_shared import ApiTestCase


def _png_upload(name: str = "coach-post.png") -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, format="PNG")
    buffer.seek(0)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


class RolePortalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            fullname="Role Admin", phone="09122000001", password="test-pass-123", is_admin=True
        )
        cls.coach = User.objects.create_user(
            fullname="Role Coach", phone="09122000002", password="test-pass-123", is_coach=True
        )
        cls.other_coach = User.objects.create_user(
            fullname="Other Coach", phone="09122000003", password="test-pass-123", is_coach=True
        )
        cls.athlete = User.objects.create_user(
            fullname="Role Athlete", phone="09122000004", password="test-pass-123", coach=cls.coach
        )

    def test_role_property_and_set_role(self):
        self.assertEqual(self.admin.role, User.Role.ADMIN)
        self.assertEqual(self.coach.role, User.Role.COACH)
        self.assertEqual(self.athlete.role, User.Role.ATHLETE)

        self.athlete.set_role(User.Role.COACH)
        self.assertTrue(self.athlete.is_coach)
        self.assertFalse(self.athlete.is_admin)
        self.assertEqual(self.athlete.role, User.Role.COACH)

        with self.assertRaises(ValueError):
            self.athlete.set_role("unknown")

    def test_coach_application_approval_promotes_and_notifies(self):
        self.client.force_login(self.athlete)
        response = self.client.post(
            reverse("register:profile_coach_application"),
            {
                "full_name": "Role Athlete",
                "phone": "09122000004",
                "biography": "مربی بدنسازی با ۱۰ سال سابقه",
            },
        )
        self.assertEqual(response.status_code, 302)
        application = CoachApplication.objects.get(user=self.athlete)
        self.assertEqual(application.status, CoachApplication.Status.PENDING)

        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("register:admin_coach_application_review", args=[application.pk]),
            {"action": "approve"},
        )
        self.assertEqual(response.status_code, 302)
        application.refresh_from_db()
        self.athlete.refresh_from_db()
        self.assertEqual(application.status, CoachApplication.Status.APPROVED)
        self.assertTrue(self.athlete.is_coach)
        self.assertTrue(Notification.objects.filter(user=self.athlete).exists())

    def test_coach_application_rejection_stores_reason_and_notifies(self):
        application = CoachApplication.objects.create(
            user=self.athlete,
            full_name="Role Athlete",
            phone="09122000004",
            biography="رزومه",
        )
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("register:admin_coach_application_review", args=[application.pk]),
            {"action": "reject", "reason": "مدارک ناقص است"},
        )
        self.assertEqual(response.status_code, 302)
        application.refresh_from_db()
        self.assertEqual(application.status, CoachApplication.Status.REJECTED)
        self.assertEqual(application.rejection_reason, "مدارک ناقص است")
        notification = Notification.objects.filter(user=self.athlete).latest("pk")
        self.assertIn("مدارک ناقص است", notification.message)

    def test_coach_cannot_open_admin_only_pages(self):
        self.client.force_login(self.coach)
        for name in ("register:admin_gym_library", "register:admin_automatic_programming"):
            with self.subTest(url=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.url, reverse("register:admin_search"))

    def test_coach_gets_coach_pages_and_athlete_is_redirected(self):
        self.client.force_login(self.coach)
        for name in (
            "register:coach_dashboard",
            "register:coach_profile",
            "register:coach_exercise_suggestions",
            "register:coach_blog",
        ):
            with self.subTest(url=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

        self.client.force_login(self.athlete)
        response = self.client.get(reverse("register:coach_dashboard"))
        self.assertEqual(response.status_code, 302)

    def test_coach_submits_exercise_suggestion_for_admin_review(self):
        self.client.force_login(self.coach)
        response = self.client.post(
            reverse("register:coach_exercise_suggestions"),
            {
                "kind": ExerciseSuggestion.Kind.EXERCISE,
                "name_fa": "پرس سینه دمبل",
                "name_en": "Dumbbell Bench Press",
                "goal": "تقویت سینه",
            },
        )
        self.assertEqual(response.status_code, 302)
        suggestion = ExerciseSuggestion.objects.get(requested_by=self.coach)
        self.assertEqual(suggestion.status, ExerciseSuggestion.Status.PENDING)

        self.client.force_login(self.admin)
        self.client.post(
            reverse("register:admin_exercise_suggestion_review", args=[suggestion.pk]),
            {"action": "reject", "reason": "تکراری است"},
        )
        suggestion.refresh_from_db()
        self.assertEqual(suggestion.status, ExerciseSuggestion.Status.REJECTED)
        self.assertEqual(suggestion.rejection_reason, "تکراری است")

    def test_admin_sets_role_in_one_action(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("register:admin_role_set", args=[self.athlete.pk]),
            {"role": User.Role.COACH},
        )
        self.assertEqual(response.status_code, 302)
        self.athlete.refresh_from_db()
        self.assertTrue(self.athlete.is_coach)
        self.assertFalse(self.athlete.is_admin)

    def test_admin_cannot_demote_self(self):
        self.client.force_login(self.admin)
        self.client.post(
            reverse("register:admin_role_set", args=[self.admin.pk]),
            {"role": User.Role.ATHLETE},
        )
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_admin)

    def test_admin_review_pages_render(self):
        self.client.force_login(self.admin)
        for name in (
            "register:admin_analytics",
            "register:admin_coach_applications",
            "register:admin_exercise_suggestions",
            "register:admin_coach_management",
            "register:admin_notifications",
        ):
            with self.subTest(url=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_coach_handles_only_own_athlete_requests(self):
        own_request = CoachRequest.objects.create(
            user=self.athlete,
            sessions_per_week=CoachRequest.SessionsPerWeek.THREE,
            wants_workout=True,
        )
        foreign_athlete = User.objects.create_user(
            fullname="Foreign Athlete", phone="09122000005", password="test-pass-123"
        )
        foreign_request = CoachRequest.objects.create(
            user=foreign_athlete,
            sessions_per_week=CoachRequest.SessionsPerWeek.TWO,
            wants_workout=True,
        )

        self.client.force_login(self.coach)
        response = self.client.post(
            reverse("register:admin_coach_request_handled", args=[own_request.pk])
        )
        self.assertEqual(response.status_code, 302)
        own_request.refresh_from_db()
        self.assertEqual(own_request.status, CoachRequest.Status.HANDLED)

        response = self.client.post(
            reverse("register:admin_coach_request_handled", args=[foreign_request.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_coach_blog_submission_waits_for_admin_approval(self):
        self.client.force_login(self.coach)
        response = self.client.post(
            reverse("register:coach_blog"),
            {
                "title": "راهنمای تمرین در خانه",
                "reading_time": 6,
                "article_excerpt": "چند نکته کوتاه",
                "article_description": "متن کامل پست بلاگ مربی",
                "image": _png_upload(),
            },
        )
        self.assertEqual(response.status_code, 302)
        article = ArticleBlogModel.objects.get(author=self.coach)
        self.assertEqual(article.publication_status, ArticleBlogModel.PublicationStatus.PENDING)

        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("register:admin_article_moderate", args=[article.pk]),
            {"action": "approve"},
        )
        self.assertEqual(response.status_code, 302)
        article.refresh_from_db()
        self.assertEqual(
            article.publication_status, ArticleBlogModel.PublicationStatus.APPROVED
        )
        self.assertTrue(
            Notification.objects.filter(
                user=self.coach, message__contains=article.title
            ).exists()
        )

    def test_admin_cannot_open_coach_blog_editor(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("register:coach_blog"))
        self.assertEqual(response.status_code, 302)

    def test_coach_can_change_own_password(self):
        self.client.force_login(self.coach)
        url = reverse("register:admin_password_change")
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(
            url,
            {
                "old_password": "test-pass-123",
                "new_password1": "FreshCoachPass987",
                "new_password2": "FreshCoachPass987",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.coach.refresh_from_db()
        self.assertTrue(self.coach.check_password("FreshCoachPass987"))


class RoleApiTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_user(
            fullname="api-role-admin", phone="09120000401", password="StrongPass123", is_admin=True
        )
        self.coach = User.objects.create_user(
            fullname="api-role-coach", phone="09120000402", password="StrongPass123", is_coach=True
        )

    def _headers(self, user):
        tokens = issue_token_pair(user=user, name="role-tests")
        return {"HTTP_AUTHORIZATION": f"Bearer {tokens['access_token']}"}

    def test_athlete_submits_coach_application(self):
        response = self.client.post(
            reverse("api:me_coach_application"),
            data={
                "full_name": "api-test-user",
                "phone": "09120000301",
                "biography": "سابقه مربیگری",
            },
            **self.headers,
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            CoachApplication.objects.get(user=self.user).status,
            CoachApplication.Status.PENDING,
        )

    def test_admin_assigns_role_via_api(self):
        response = self.client.patch(
            reverse("api:admin_user_role", kwargs={"user_id": self.other_user.pk}),
            data='{"role": "coach"}',
            content_type="application/json",
            **self._headers(self.admin),
        )
        self.assertEqual(response.status_code, 200)
        self.other_user.refresh_from_db()
        self.assertTrue(self.other_user.is_coach)

    def test_non_admin_cannot_list_coach_applications(self):
        response = self.client.get(
            reverse("api:admin_coach_applications"), **self.headers
        )
        self.assertEqual(response.status_code, 403)

    def test_coach_lists_own_exercise_suggestions(self):
        ExerciseSuggestion.objects.create(
            requested_by=self.coach,
            kind=ExerciseSuggestion.Kind.EXERCISE,
            name_fa="حرکت",
            name_en="Movement",
            goal="هدف",
        )
        response = self.client.get(
            reverse("api:coach_exercise_suggestions"), **self._headers(self.coach)
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["data"]["items"]), 1)
