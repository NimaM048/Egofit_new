"""Web coach-portal access rules.

A pure coach must only reach their own athletes through the staff portal,
while administrators keep unrestricted access. These tests pin that boundary
because the portal reuses the admin templates.
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from account.models import CoachRequest, User


class CoachPortalAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            fullname="Portal Admin",
            phone="09121000001",
            password="test-pass-123",
            is_admin=True,
        )
        cls.coach = User.objects.create_user(
            fullname="Portal Coach",
            phone="09121000002",
            password="test-pass-123",
            is_coach=True,
        )
        cls.other_coach = User.objects.create_user(
            fullname="Other Coach",
            phone="09121000003",
            password="test-pass-123",
            is_coach=True,
        )
        cls.athlete = User.objects.create_user(
            fullname="Own Athlete",
            phone="09121000004",
            password="test-pass-123",
            coach=cls.coach,
        )
        cls.foreign_athlete = User.objects.create_user(
            fullname="Foreign Athlete",
            phone="09121000005",
            password="test-pass-123",
            coach=cls.other_coach,
        )

    def test_admin_reaches_full_portal(self):
        self.client.force_login(self.admin)
        for name, args in (
            ("register:admin_search", []),
            ("register:admin_user_list", []),
            ("register:admin_user_hub", [self.foreign_athlete.pk]),
            ("register:admin_gym_library", []),
            ("register:admin_automatic_programming", []),
            ("register:admin_coach_management", []),
        ):
            with self.subTest(url=name):
                self.assertEqual(
                    self.client.get(reverse(name, args=args)).status_code, 200
                )

    def test_coach_sees_only_own_roster(self):
        self.client.force_login(self.coach)
        response = self.client.get(reverse("register:admin_search"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Own Athlete", body)
        self.assertNotIn("Foreign Athlete", body)

    def test_coach_can_open_own_athlete(self):
        self.client.force_login(self.coach)
        for name in (
            "register:admin_user_hub",
            "register:admin_user_analysis",
            "register:admin_circumference",
            "register:admin_caliper",
            "register:admin_health",
            "register:admin_program_list",
            "register:admin_upload",
        ):
            with self.subTest(url=name):
                response = self.client.get(reverse(name, args=[self.athlete.pk]))
                self.assertEqual(response.status_code, 200)

    def test_coach_cannot_open_foreign_athlete(self):
        self.client.force_login(self.coach)
        for name in (
            "register:admin_user_hub",
            "register:admin_user_analysis",
            "register:admin_circumference",
            "register:admin_program_list",
        ):
            with self.subTest(url=name):
                response = self.client.get(
                    reverse(name, args=[self.foreign_athlete.pk])
                )
                self.assertEqual(response.status_code, 404)

    def test_coach_redirected_from_admin_only_pages(self):
        self.client.force_login(self.coach)
        for name in (
            "register:admin_user_list",
            "register:admin_gym_library",
            "register:admin_automatic_programming",
            "register:admin_coach_management",
        ):
            with self.subTest(url=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 302)

    def test_admin_can_enable_automatic_programming_for_one_coach(self):
        self.assertFalse(self.coach.can_use_automatic_programming)
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("register:admin_coach_management"),
            {
                "action": "set_automatic_programming",
                "coach_id": self.coach.pk,
                "enabled": "1",
            },
        )
        self.assertRedirects(response, reverse("register:admin_coach_management"))
        self.coach.refresh_from_db()
        self.other_coach.refresh_from_db()
        self.assertTrue(self.coach.can_use_automatic_programming)
        self.assertFalse(self.other_coach.can_use_automatic_programming)

        self.client.force_login(self.coach)
        response = self.client.get(reverse("register:admin_automatic_programming"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Own Athlete")
        self.assertNotContains(response, "Foreign Athlete")

    def test_enabled_coach_cannot_generate_for_foreign_athlete(self):
        self.coach.can_use_automatic_programming = True
        self.coach.save(update_fields=["can_use_automatic_programming"])
        self.client.force_login(self.coach)
        response = self.client.post(
            reverse("register:admin_automatic_programming"),
            {"action": "generate", "user": self.foreign_athlete.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Foreign Athlete")

    def test_coach_login_lands_on_coach_dashboard(self):
        response = self.client.post(
            reverse("register:pass_login"),
            {"fullname": self.coach.fullname, "password": "test-pass-123"},
        )
        self.assertRedirects(response, reverse("register:coach_dashboard"))

    def test_athlete_never_reaches_staff_portal(self):
        self.client.force_login(self.athlete)
        response = self.client.get(reverse("register:admin_search"))
        self.assertEqual(response.status_code, 302)


class CoachPortalSelfServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.coach = User.objects.create_user(
            fullname="Self Service Coach",
            phone="09121110001",
            password="test-pass-123",
            is_coach=True,
        )
        cls.second_coach = User.objects.create_user(
            fullname="Second Self Service Coach",
            phone="09121110002",
            password="test-pass-123",
            is_coach=True,
        )
        cls.admin_coach = User.objects.create_user(
            fullname="Admin Coach",
            phone="09121110008",
            password="test-pass-123",
            is_admin=True,
            is_coach=True,
        )
        cls.inactive_coach = User.objects.create_user(
            fullname="Inactive Coach",
            phone="09121110003",
            is_coach=True,
            is_active=False,
        )
        cls.regular_user = User.objects.create_user(
            fullname="Regular Member",
            phone="09121110004",
        )
        cls.athlete = User.objects.create_user(
            fullname="Self Service Athlete",
            phone="09121110005",
        )

    def test_athlete_can_choose_an_active_coach_and_route_pending_request(self):
        pending = CoachRequest.objects.create(
            user=self.athlete,
            sessions_per_week="3",
            wants_workout=True,
        )
        self.client.force_login(self.athlete)

        page = self.client.get(reverse("register:profile_coach"))
        self.assertContains(page, "Self Service Coach")
        self.assertContains(page, "Second Self Service Coach")
        self.assertContains(page, "Admin Coach")
        self.assertNotContains(page, "Inactive Coach")
        self.assertNotContains(page, "Regular Member")

        response = self.client.post(
            reverse("register:profile_coach"),
            {"action": "select_coach", "coach": self.coach.pk},
        )
        self.assertRedirects(response, reverse("register:profile_coach"))
        self.athlete.refresh_from_db()
        self.assertEqual(self.athlete.coach_id, self.coach.pk)

        self.client.force_login(self.coach)
        dashboard = self.client.get(reverse("register:coach_dashboard"))
        self.assertEqual(dashboard.context["pending_requests"], 1)
        self.assertEqual(dashboard.context["pending_request_items"][0].pk, pending.pk)
        self.assertContains(dashboard, "Self Service Athlete")

    def test_athlete_cannot_assign_an_inactive_or_non_coach_account(self):
        self.client.force_login(self.athlete)

        for invalid_coach in (self.inactive_coach, self.regular_user):
            with self.subTest(coach=invalid_coach.fullname):
                response = self.client.post(
                    reverse("register:profile_coach"),
                    {"action": "select_coach", "coach": invalid_coach.pk},
                )
                self.assertEqual(response.status_code, 200)
                self.assertIn("coach", response.context["coach_selection_form"].errors)

        self.athlete.refresh_from_db()
        self.assertIsNone(self.athlete.coach_id)

    def test_coach_can_create_an_athlete_only_on_their_own_roster(self):
        self.client.force_login(self.coach)
        add_url = reverse("register:coach_athlete_add")
        self.assertEqual(self.client.get(add_url).status_code, 200)

        response = self.client.post(
            add_url,
            {
                "first_name": "New",
                "last_name": "Athlete",
                "phone": "09121110006",
                "password": "athlete-pass-123",
                "is_admin": "on",
                "is_coach": "on",
                "coach": self.second_coach.pk,
            },
        )

        athlete = User.objects.get(phone="09121110006")
        self.assertRedirects(
            response,
            reverse("register:admin_user_hub", kwargs={"user_id": athlete.pk}),
        )
        self.assertEqual(athlete.coach_id, self.coach.pk)
        self.assertFalse(athlete.is_admin)
        self.assertFalse(athlete.is_coach)
        self.assertTrue(athlete.check_password("athlete-pass-123"))

    def test_admin_cannot_use_the_coach_athlete_creation_page(self):
        admin = User.objects.create_user(
            fullname="Self Service Admin",
            phone="09121110007",
            is_admin=True,
        )
        self.client.force_login(admin)
        response = self.client.get(reverse("register:coach_athlete_add"))
        self.assertRedirects(response, reverse("register:admin_search"))
