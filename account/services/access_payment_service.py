from __future__ import annotations

from dataclasses import dataclass

from django.db import IntegrityError, transaction
from django.utils import timezone

from account.models import ClientDocument, ClientDocumentPayment, WorkoutProgram, WorkoutProgramPayment
from cart.exceptions import PaymentProviderException, PaymentVerificationException
from cart.providers.zarinpal_provider import ZarinPalPaymentProvider


@dataclass(frozen=True)
class AccessPaymentInitiation:
    payment_id: int | None
    redirect_url: str | None
    already_paid: bool = False


@dataclass(frozen=True)
class AccessPaymentVerification:
    payment: ClientDocumentPayment | WorkoutProgramPayment | None
    state: str
    ref_id: str | None = None
    message: str | None = None


class AccessPaymentService:
    """Own concurrency and idempotency for paid documents and workout programs."""

    def __init__(self, provider: ZarinPalPaymentProvider | None = None):
        self.provider = provider or ZarinPalPaymentProvider()

    @transaction.atomic
    def initiate_document(self, *, document_id: int, user, callback_url: str) -> AccessPaymentInitiation:
        document = ClientDocument.objects.select_for_update().get(pk=document_id, user=user)
        return self._initiate(
            target=document,
            user=user,
            payment_model=ClientDocumentPayment,
            target_field="document",
            callback_url=callback_url,
            description=f"دریافت فایل {document.title}",
            target_amount=document.price,
        )

    @transaction.atomic
    def initiate_program(self, *, program_id: int, user, callback_url: str) -> AccessPaymentInitiation:
        program = WorkoutProgram.objects.select_for_update().get(
            pk=program_id,
            user=user,
            is_published=True,
        )
        return self._initiate(
            target=program,
            user=user,
            payment_model=WorkoutProgramPayment,
            target_field="program",
            callback_url=callback_url,
            description=f"دسترسی به برنامه {program.title}",
            target_amount=program.price,
        )

    def _initiate(
        self,
        *,
        target,
        user,
        payment_model,
        target_field: str,
        callback_url: str,
        description: str,
        target_amount: int,
    ) -> AccessPaymentInitiation:
        filters = {
            target_field: target,
            "user": user,
            "status": payment_model.Status.PAID,
        }
        if payment_model.objects.filter(**filters).exists():
            return AccessPaymentInitiation(payment_id=None, redirect_url=None, already_paid=True)

        payment = self._get_or_create_initiated_payment(
            payment_model=payment_model,
            target_field=target_field,
            target=target,
            user=user,
            amount=target_amount,
            responsible=self._responsible_user(target=target, target_field=target_field),
        )

        if payment.authority:
            return AccessPaymentInitiation(
                payment_id=payment.pk,
                redirect_url=self.provider.build_startpay_url(payment.authority),
            )

        result = self.provider.request_payment(
            amount=payment.amount,
            callback_url=callback_url,
            description=description,
            mobile=getattr(user, "phone", None),
            email=getattr(user, "email", None) or None,
        )
        payment.authority = result.authority
        payment.status = payment_model.Status.INITIATED
        payment.save(update_fields=["authority", "status"])
        return AccessPaymentInitiation(payment_id=payment.pk, redirect_url=result.redirect_url)

    @staticmethod
    def _get_or_create_initiated_payment(*, payment_model, target_field, target, user, amount, responsible=None):
        """Return the single active payment for a target, creating it if needed.

        The caller already holds a ``select_for_update`` lock on the paid target,
        which serialises concurrent requests on MySQL (where the conditional
        ``UniqueConstraint`` is silently dropped). The ``IntegrityError``
        fallback keeps the same guarantee on backends that do enforce it.
        """
        lookup = {
            target_field: target,
            "user": user,
            "status": payment_model.Status.INITIATED,
        }
        payment = payment_model.objects.select_for_update().filter(**lookup).first()
        if payment is not None:
            if payment.responsible_id is None and responsible is not None:
                payment.responsible = responsible
                payment.save(update_fields=["responsible"])
            return payment
        try:
            return payment_model.objects.create(
                **{
                    target_field: target,
                    "user": user,
                    "amount": amount,
                    "responsible": responsible,
                }
            )
        except IntegrityError:
            payment = payment_model.objects.filter(**lookup).first()
            if payment is None:
                raise
            return payment

    @staticmethod
    def _responsible_user(*, target, target_field):
        responsible = getattr(target, "prescribed_by", None) if target_field == "program" else getattr(target, "uploaded_by", None)
        if responsible and (responsible.is_admin or responsible.is_coach):
            return responsible
        return None

    @transaction.atomic
    def verify_document(self, *, authority: str, status: str) -> AccessPaymentVerification:
        return self._verify(
            authority=authority,
            status=status,
            payment_model=ClientDocumentPayment,
        )

    @transaction.atomic
    def verify_program(self, *, authority: str, status: str) -> AccessPaymentVerification:
        return self._verify(
            authority=authority,
            status=status,
            payment_model=WorkoutProgramPayment,
        )

    def _verify(self, *, authority: str, status: str, payment_model) -> AccessPaymentVerification:
        payment = payment_model.objects.select_for_update().filter(authority=authority).first()
        if payment is None:
            return AccessPaymentVerification(payment=None, state="missing")
        if payment.status == payment_model.Status.PAID:
            return AccessPaymentVerification(payment=payment, state="already_paid", ref_id=payment.ref_id)
        if payment.status != payment_model.Status.INITIATED:
            return AccessPaymentVerification(payment=payment, state="failed", message="Payment is not active.")
        if status != "OK":
            payment.status = payment_model.Status.FAILED
            payment.save(update_fields=["status"])
            return AccessPaymentVerification(payment=payment, state="failed")

        try:
            result = self.provider.verify_payment(amount=payment.amount, authority=authority)
        except PaymentVerificationException as exc:
            payment.status = payment_model.Status.FAILED
            payment.save(update_fields=["status"])
            return AccessPaymentVerification(payment=payment, state="failed", message=str(exc))

        if not result.success:
            payment.status = payment_model.Status.FAILED
            payment.save(update_fields=["status"])
            return AccessPaymentVerification(payment=payment, state="failed", message=result.message)

        payment.status = payment_model.Status.PAID
        payment.ref_id = result.ref_id or ""
        payment.paid_at = timezone.now()
        payment.save(update_fields=["status", "ref_id", "paid_at"])
        return AccessPaymentVerification(payment=payment, state="paid", ref_id=payment.ref_id)
