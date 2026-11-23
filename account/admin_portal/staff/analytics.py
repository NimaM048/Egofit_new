"""Staff financial and platform analytics."""

from __future__ import annotations

from django.db.models import Count
from django.views.generic import TemplateView

from account.models import BodyCircumferenceMeasurement, CaliperMeasurement, User
from account.services.admin_financial_analytics_service import AdminFinancialAnalyticsService

from .mixins import AdminPageMixin


class AdminAnalyticsView(AdminPageMixin, TemplateView):
    """Site-wide people and financial analytics for the administrator."""

    template_name = "admin_portal/analytics.html"
    active_section = "analytics"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        finance_date_error = ""
        try:
            financial_analytics = AdminFinancialAnalyticsService.dashboard_data(
                self.request.GET.get("from_date"),
                self.request.GET.get("to_date"),
            )
        except ValueError as exc:
            finance_date_error = str(exc)
            financial_analytics = AdminFinancialAnalyticsService.dashboard_data()
        athletes = User.objects.filter(is_admin=False, is_coach=False, is_active=True)
        coaches = User.objects.filter(is_coach=True, is_active=True)
        coach_rows = list(
            coaches.annotate(athlete_count=Count("athletes")).order_by("-athlete_count", "fullname")
        )
        max_athletes = max((coach.athlete_count for coach in coach_rows), default=0) or 1
        for coach in coach_rows:
            coach.bar_percent = round(coach.athlete_count * 100 / max_athletes, 1)
        context.update(
            {
                "total_athletes": athletes.count(),
                "total_coaches": coaches.count(),
                "total_admins": User.objects.filter(is_admin=True, is_active=True).count(),
                "assigned_athletes": athletes.filter(coach__isnull=False).count(),
                "unassigned_athletes": athletes.filter(coach__isnull=True).count(),
                "total_measurements": BodyCircumferenceMeasurement.objects.count(),
                "total_caliper": CaliperMeasurement.objects.count(),
                "coach_rows": coach_rows,
                "financial_analytics": financial_analytics,
                "finance_date_error": finance_date_error,
                "athlete_rows": list(
                    athletes.select_related("coach").order_by("-id")[:50]
                ),
            }
        )
        return context
