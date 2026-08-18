"""Бизнес-логика рассрочки: всё, что не является чтением одной модели."""

from datetime import date
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from django.db import transaction

from apps.order.choices import OrderStatus
from apps.order.models import Order, Payment


class InstallmentError(Exception):
    """Рассрочку выдать нельзя."""


@transaction.atomic
def build_schedule(order: Order, first_due: date = None):
    """Строит график платежей и активирует рассрочку.

    Остаток от округления добавляется к первому платежу, чтобы сумма
    графика в точности равнялась order.total.
    """
    if order.status != OrderStatus.NEW:
        raise InstallmentError("График строится только для нового заказа")
    if order.payments.exists():
        raise InstallmentError("График уже построен")
    if order.total > order.client.available_limit:
        raise InstallmentError("Превышен лимит клиента")

    first_due = first_due or date.today() + relativedelta(months=1)
    part = (order.total / order.months).quantize(Decimal("0.01"))
    rest = order.total - part * order.months
    Payment.objects.bulk_create(
        Payment(
            order=order,
            number=i + 1,
            due_date=first_due + relativedelta(months=i),
            amount=part + (rest if i == 0 else 0),
        )
        for i in range(order.months)
    )
    order.status = OrderStatus.ACTIVE
    order.save(update_fields=["status", "updated_at"])
    return order.payments.all()


def cancel(order: Order):
    """Отмена заказа. Оплаченный платёж — стоп."""
    if order.paid:
        raise InstallmentError("По заказу есть оплаты, отмена невозможна")
    order.payments.all().delete()
    order.status = OrderStatus.CANCELED
    order.save(update_fields=["status", "updated_at"])
    return order
