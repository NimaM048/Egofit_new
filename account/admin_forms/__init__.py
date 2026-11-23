"""Compatibility exports for the staff and athlete form modules."""

from .fields import JalaliDateField, _jalali_date_widget
from .users import (
    AdminUserSearchForm,
    AdminPersonalInfoForm,
    AdminUserCreateForm,
    CoachAthleteCreateForm,
    AdminRoleToggleForm,
    AdminCoachRoleToggleForm,
    AdminBulkCoachAssignForm,
    AdminUserCoachForm,
    AdminRoleSetForm,
)
from .measurements import (
    AdminHealthRecordForm,
    DailyMoodForm,
    _clean_measurement_date,
    AdminCircumferenceForm,
    AdminCaliperForm,
)
from .files import AdminClientMediaForm, AdminClientDocumentForm
from .reviews import (
    AdminCommentModerationForm,
    AdminReviewForm,
    AdminCoachApplicationReviewForm,
    AdminExerciseSuggestionReviewForm,
)
from .exercise_library import (
    LibrarySearchForm,
    MuscleForm,
    LookupCatalogModelForm,
    build_lookup_form,
    ExerciseForm,
    CardioExerciseForm,
    CorrectiveExerciseForm,
    MuscleVolumeLandmarkForm,
)
from .programs import (
    WorkoutProgramForm,
    JalaliWorkoutProgramForm,
    WorkoutProgramPayloadForm,
    automatic_programming_user_queryset,
    AutomaticProgrammingForm,
    ProgramTemplateForm,
)


__all__ = [
    "AdminBulkCoachAssignForm",
    "AdminCaliperForm",
    "AdminCircumferenceForm",
    "AdminClientDocumentForm",
    "AdminClientMediaForm",
    "AdminCoachApplicationReviewForm",
    "AdminCoachRoleToggleForm",
    "AdminCommentModerationForm",
    "AdminExerciseSuggestionReviewForm",
    "AdminHealthRecordForm",
    "AdminPersonalInfoForm",
    "AdminReviewForm",
    "AdminRoleSetForm",
    "AdminRoleToggleForm",
    "AdminUserCoachForm",
    "AdminUserCreateForm",
    "AdminUserSearchForm",
    "AutomaticProgrammingForm",
    "CardioExerciseForm",
    "CoachAthleteCreateForm",
    "CorrectiveExerciseForm",
    "DailyMoodForm",
    "ExerciseForm",
    "JalaliDateField",
    "JalaliWorkoutProgramForm",
    "LibrarySearchForm",
    "LookupCatalogModelForm",
    "MuscleForm",
    "MuscleVolumeLandmarkForm",
    "ProgramTemplateForm",
    "WorkoutProgramForm",
    "WorkoutProgramPayloadForm",
    "automatic_programming_user_queryset",
    "build_lookup_form",
]
