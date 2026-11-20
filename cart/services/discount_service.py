from __future__ import annotations

from django.db import transaction
from django.utils.translation import gettext_lazy as _

from cart.exceptions import DiscountCodeException
from cart.models import DiscountCode
from cart.repositories.discount_repository import DiscountRepository
from cart.repositories.order_repository import OrderRepository


class DiscountService:
    def __init__(self, order_repository: OrderRepository | None = None, discount_repository: DiscountRepository | None = None):
        self.order_repository = order_repository or OrderRepository()
        self.discount_repository = discount_repository or DiscountRepository()

    @transaction.atomic
    def apply_discount(self, *, order_id: int, user, raw_code: str):
        code = (raw_code or "").strip().upper()
        if not code:
            raise DiscountCodeException(_("کد تخفیف را وارد کنید."))

        order = self.order_repository.get_order_for_user(order_id=order_id, user=user, is_paid=False, lock=True)
        if order is None:
            raise DiscountCodeException(_("سفارش مورد نظر پیدا نشد."))

        if order.applied_discount_code_id:
            raise DiscountCodeException(_("برای این سفارش قبلاً کد تخفیف اعمال شده است."))

        try:
            discount_code = self.discount_repository.get_active_code_for_update(code=code)
        except DiscountCode.DoesNotExist as exc:
            raise DiscountCodeException(_("کد تخفیف نامعتبر است.")) from exc

        if discount_code.quantity <= 0:
            raise DiscountCodeException(_("این کد تخفیف قابل استفاده نیست."))

        assignment = self.discount_repository.get_user_assignment_for_update(
            user=user,
            discount_code=discount_code,
        )
        if not discount_code.is_public and assignment is None:
            raise DiscountCodeException(_("این کد تخفیف برای حساب شما تعریف نشده است."))

        max_uses = discount_code.max_uses_per_user or 0
        if max_uses and assignment is not None and assignment.usage_count >= max_uses:
            raise DiscountCodeException(_("سقف استفاده از این کد تخفیف برای شما تکمیل شده است."))

        discount_amount = int(discount_code.percentage * order.subtotal_price / 100)
        discount_amount = min(discount_amount, order.subtotal_price)
        order.discount_amount = discount_amount
        order.total_price = max(order.subtotal_price - discount_amount, 0)
        order.applied_discount_code = discount_code
        order.save(update_fields=["discount_amount", "total_price", "applied_discount_code"])

        assignment = assignment or self.discount_repository.get_or_create_user_assignment(
            user=user,
            discount_code=discount_code,
        )
        self.discount_repository.register_usage(assignment=assignment, discount_code=discount_code)

        return order, discount_amount
