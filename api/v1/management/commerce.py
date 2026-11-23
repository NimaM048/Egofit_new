"""Staff transaction reporting."""

from __future__ import annotations

from account.models import ClientDocumentPayment, WorkoutProgramPayment
from api.decorators import api_auth_required, api_endpoint, api_methods
from api.responses import ok
from api.utils import json_value, parse_int
from api.v1.permissions import api_admin_required
from cart.models import Order

from .support import _audit, _page


@api_endpoint
@api_methods("GET")
@api_auth_required
@api_admin_required
def admin_transactions(request):
    _audit(request.user, "transactions.list.read", object_type="transaction_list")
    queryset = Order.objects.select_related("user").prefetch_related("items__product").order_by("-created_at")
    status = (request.GET.get("status") or "").strip()
    if status in {choice for choice, _label in Order.PaymentStatus.choices}:
        queryset = queryset.filter(status=status)
    if request.GET.get("user_id"):
        queryset = queryset.filter(user_id=parse_int(request.GET["user_id"], field="user_id"))
    orders = _page(request, queryset, lambda order: {
        "id": order.pk,
        "order_number": order.order_number,
        "user": {"id": order.user_id, "fullname": order.user.fullname, "phone": order.user.phone},
        "subtotal_price": order.subtotal_price,
        "discount_amount": order.discount_amount,
        "total_price": order.total_price,
        "status": order.status,
        "is_paid": order.is_paid,
        "created_at": json_value(order.created_at),
        "paid_at": json_value(order.paid_at),
        "ref_id": order.ref_id,
        "items": [{"course_id": item.product_id, "title": item.product.title, "price": item.price} for item in order.items.all()],
    })
    document_payments = ClientDocumentPayment.objects.select_related("user", "document").order_by("-created_at")
    program_payments = WorkoutProgramPayment.objects.select_related("user", "program").order_by("-created_at")
    if status in {choice for choice, _label in ClientDocumentPayment.Status.choices}:
        document_payments = document_payments.filter(status=status)
    if status in {choice for choice, _label in WorkoutProgramPayment.Status.choices}:
        program_payments = program_payments.filter(status=status)
    if request.GET.get("user_id"):
        user_id = parse_int(request.GET["user_id"], field="user_id")
        document_payments = document_payments.filter(user_id=user_id)
        program_payments = program_payments.filter(user_id=user_id)
    return ok({
        "orders": orders,
        "document_payments": _page(request, document_payments, lambda payment: {
            "id": payment.pk,
            "user": {"id": payment.user_id, "fullname": payment.user.fullname, "phone": payment.user.phone},
            "document": {"id": payment.document_id, "title": payment.document.title},
            "amount": payment.amount,
            "status": payment.status,
            "created_at": json_value(payment.created_at),
            "paid_at": json_value(payment.paid_at),
            "ref_id": payment.ref_id,
        }),
        "workout_program_payments": _page(request, program_payments, lambda payment: {
            "id": payment.pk,
            "user": {"id": payment.user_id, "fullname": payment.user.fullname, "phone": payment.user.phone},
            "program": {"id": payment.program_id, "title": payment.program.title},
            "amount": payment.amount,
            "status": payment.status,
            "created_at": json_value(payment.created_at),
            "paid_at": json_value(payment.paid_at),
            "ref_id": payment.ref_id,
        }),
    })
