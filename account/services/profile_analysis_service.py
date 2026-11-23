"""Stable service interface for athlete profile and dashboard analysis."""

from account.services.profile_analysis.dashboard import (
    get_analysis_dashboard_data,
    get_dashboard_body_composition_data,
)
from account.services.profile_analysis.measurements import (
    BODY_FAT_FORMULA_OPTIONS,
    CALIPER_FIELD_LABELS,
    CIRCUMFERENCE_FIELD_LABELS,
    get_analysis_metric_series,
)
from account.services.profile_analysis.panel import get_analysis_context
from account.services.profile_analysis.performance import get_performance_chart_data
from account.services.profile_analysis.presentation import ANALYSIS_METRIC_OPTIONS


class ProfileAnalysisService:
    """Expose analysis to web and API callers through the existing service."""

    CIRCUMFERENCE_FIELD_LABELS = CIRCUMFERENCE_FIELD_LABELS
    CALIPER_FIELD_LABELS = CALIPER_FIELD_LABELS
    ANALYSIS_METRIC_OPTIONS = ANALYSIS_METRIC_OPTIONS
    BODY_FAT_FORMULA_OPTIONS = BODY_FAT_FORMULA_OPTIONS

    get_analysis_context = staticmethod(get_analysis_context)
    get_analysis_dashboard_data = staticmethod(get_analysis_dashboard_data)
    get_analysis_metric_series = staticmethod(get_analysis_metric_series)
    get_dashboard_body_composition_data = staticmethod(get_dashboard_body_composition_data)
    get_performance_chart_data = staticmethod(get_performance_chart_data)
