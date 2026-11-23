from django.urls import path

from account.documents import views as document_views

from . import details, overview, performance


urlpatterns = [
    path("profile/workout-programs/", overview.ProfileWorkoutProgramsView.as_view(), name="profile_workout_programs"),
    path("profile/workout-programs/<int:pk>/performance/", performance.ProfileWorkoutProgramPerformanceView.as_view(), name="profile_workout_program_performance"),
    path("profile/workout-programs/<int:pk>/pay/", document_views.WorkoutProgramPaymentView.as_view(), name="profile_workout_program_pay"),
    path("profile/workout-programs/payment/verify/", document_views.WorkoutProgramPaymentVerifyView.as_view(), name="profile_workout_program_verify"),
    path("profile/workout-programs/<int:pk>/pdf/", details.ProfileWorkoutProgramPdfView.as_view(), name="profile_workout_program_pdf"),
    path("profile/workout-programs/movement/<slug:kind>/<int:movement_id>/", details.ProfileWorkoutMovementView.as_view(), name="profile_workout_movement"),
]
