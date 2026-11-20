from django.db.models import Q

from account.models import ProgramTemplate
from account.portal_mixins import is_coach_only


class ProgramTemplateService:
    @staticmethod
    def accessible_to(user):
        queryset = ProgramTemplate.objects.select_related("difficulty", "owner").prefetch_related("shared_with")
        if getattr(user, "is_admin", False):
            return queryset
        if not is_coach_only(user) or not getattr(user, "can_use_automatic_programming", False):
            return queryset.none()
        return queryset.filter(
            Q(owner=user)
            | Q(shared_with=user)
            | Q(is_shared_with_enabled_coaches=True)
        ).distinct()

    @staticmethod
    def generation_payload(template):
        payload = template.payload if isinstance(template.payload, dict) else {}
        return {
            "session_movement_targets": payload.get("session_movement_targets") or [],
            "target_secondary_movement_type_ids": payload.get("target_secondary_movement_type_ids") or [],
            "secondary_movement_counts": payload.get("secondary_movement_counts") or {},
        }
