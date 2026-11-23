"""Compatibility imports; use account.profile.views for new code."""

from account.profile.views import (
    ProfileView,
    ProfileMoodView,
    ProfileAnalysisView,
    ProfileCoachView,
    ProfileMetricEditView,
    AthleteTrainingProfileView,
    ProfileCoursesView,
    ProfileDocumentsView,
    AddCourseToProfileView,
    ProfileFinancialView,
    ProfileCommentsView,
    ProfileNotificationsView,
    edit_user_profile,
    ProfileCoachApplicationView,
)


__all__ = [
    "AddCourseToProfileView",
    "AthleteTrainingProfileView",
    "ProfileAnalysisView",
    "ProfileCoachApplicationView",
    "ProfileCoachView",
    "ProfileCommentsView",
    "ProfileCoursesView",
    "ProfileDocumentsView",
    "ProfileFinancialView",
    "ProfileMetricEditView",
    "ProfileMoodView",
    "ProfileNotificationsView",
    "ProfileView",
    "edit_user_profile",
]
