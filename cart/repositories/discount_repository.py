from __future__ import annotations

from django.utils import timezone

from cart.models import DiscountCode, UserDiscountCode


class DiscountRepository:
    def get_active_code_for_update(self, *, code: str):
        return DiscountCode.objects.select_for_update().get(name=code, is_active=True)

    def user_has_used_code(self, *, user, discount_code: DiscountCode) -> bool:
        return UserDiscountCode.objects.filter(user=user, discount_code=discount_code).exists()

    def create_user_discount_code(self, *, user, discount_code: DiscountCode) -> UserDiscountCode:
        return UserDiscountCode.objects.create(user=user, discount_code=discount_code)

    def get_user_assignment_for_update(self, *, user, discount_code: DiscountCode) -> UserDiscountCode | None:
        return (
            UserDiscountCode.objects.select_for_update()
            .filter(user=user, discount_code=discount_code)
            .first()
        )

    def get_or_create_user_assignment(self, *, user, discount_code: DiscountCode) -> UserDiscountCode:
        assignment, _ = UserDiscountCode.objects.get_or_create(user=user, discount_code=discount_code)
        return assignment

    def register_usage(self, *, assignment: UserDiscountCode, discount_code: DiscountCode) -> None:
        assignment.usage_count = (assignment.usage_count or 0) + 1
        assignment.last_used_at = timezone.now()
        assignment.save(update_fields=["usage_count", "last_used_at"])

        discount_code.used_count = (discount_code.used_count or 0) + 1
        discount_code.quantity = max(discount_code.quantity - 1, 0)
        discount_code.save(update_fields=["used_count", "quantity"])
