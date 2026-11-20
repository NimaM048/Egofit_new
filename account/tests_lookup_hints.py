import json

from django.test import TestCase
from django.urls import reverse

from account.domain.workout_prescriptions import is_valid_repetition_prescription
from account.models import ExerciseTrainingMethod, User


class RepetitionLookupHelpTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            fullname="Lookup Admin",
            phone="09120001001",
            is_admin=True,
        )

    def test_method_help_examples_match_repetition_validation(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("register:admin_lookup_add", args=["repetition-type"])
        )

        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        help_by_method = json.loads(
            form.fields["method"].widget.attrs["data-repetition-help"]
        )
        examples = {
            ExerciseTrainingMethod.NORMAL: "6-12",
            ExerciseTrainingMethod.PYRAMID: "5.10.15.10.5",
            ExerciseTrainingMethod.REVERSE_PYRAMID: "15.10.5.10.15",
            ExerciseTrainingMethod.LOW_TO_HIGH: "25+20+15+12+8",
            ExerciseTrainingMethod.HIGH_TO_LOW: "8+12+15+20+25",
            ExerciseTrainingMethod.DROP_SET: "5>8",
            ExerciseTrainingMethod.TWENTY_ONE_REPS: "7+7+7",
        }

        self.assertEqual(set(help_by_method), set(examples))
        self.assertEqual(
            form.fields["reps"].help_text,
            help_by_method[ExerciseTrainingMethod.NORMAL],
        )
        self.assertContains(response, "data-repetition-help-text")
        for method, value in examples.items():
            with self.subTest(method=method):
                self.assertTrue(
                    is_valid_repetition_prescription(value, method, goal="volume")
                )
