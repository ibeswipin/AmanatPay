from datetime import date
from decimal import Decimal

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.order.choices import OrderStatus
from apps.utils.models import BaseModel


class Order(BaseModel):
    merchant = models.ForeignKey(
        "merchant.Merchant", verbose_name=_("Мерчант"), on_delete=models.PROTECT, related_name="orders"
    )
    client = models.ForeignKey(
        "client.Client", verbose_name=_("Клиент"), on_delete=models.PROTECT, related_name="orders"
    )
    amount = models.DecimalField(_("Сумма заказа"), max_digits=12, decimal_places=2)
    months = models.PositiveSmallIntegerField(_("Срок, мес."), default=3)
    markup_percent = models.DecimalField(
        _("Наценка за весь срок, %"), max_digits=5, decimal_places=2, default=0
    )
    status = models.CharField(_("Статус"), max_length=16, choices=OrderStatus.choices, default=OrderStatus.NEW)

    class Meta(BaseModel.Meta):
        verbose_name = _("Заказ")
        verbose_name_plural = _("Заказы")

    def __str__(self):
        return f"Заказ #{self.pk} — {self.client_id} — {self.amount}"

    @property
    def total(self) -> Decimal:
        """Сумма к возврату с наценкой."""
        markup = Decimal(self.markup_percent)
        return (Decimal(self.amount) * (1 + markup / 100)).quantize(Decimal("0.01"))

    @property
    def paid(self) -> Decimal:
        return self.payments.filter(paid_at__isnull=False).aggregate(s=models.Sum("amount"))["s"] or Decimal("0")

    @property
    def remainder(self) -> Decimal:
        return self.total - self.paid

    @property
    def merchant_payout(self) -> Decimal:
        """Сколько платим мерчанту: сумма заказа минус его комиссия."""
        commission = Decimal(self.merchant.commission_percent)
        return (Decimal(self.amount) * (1 - commission / 100)).quantize(Decimal("0.01"))


class Payment(BaseModel):
    order = models.ForeignKey(Order, verbose_name=_("Заказ"), on_delete=models.CASCADE, related_name="payments")
    number = models.PositiveSmallIntegerField(_("Номер платежа"))
    due_date = models.DateField(_("Срок оплаты"))
    amount = models.DecimalField(_("Сумма"), max_digits=12, decimal_places=2)
    paid_at = models.DateTimeField(_("Оплачен"), null=True, blank=True)

    class Meta:
        verbose_name = _("Платёж")
        verbose_name_plural = _("График платежей")
        ordering = ["order", "number"]
        constraints = [models.UniqueConstraint(fields=["order", "number"], name="uniq_payment_number_per_order")]

    def __str__(self):
        return f"{self.order_id}/{self.number} — {self.amount} до {self.due_date}"

    @property
    def is_overdue(self) -> bool:
        return self.paid_at is None and self.due_date < date.today()

    def pay(self):
        """Отметить платёж оплаченным; закрыть заказ, если он последний."""
        if self.paid_at:
            return self
        self.paid_at = timezone.now()
        self.save(update_fields=["paid_at", "updated_at"])
        if not self.order.payments.filter(paid_at__isnull=True).exists():
            self.order.status = OrderStatus.CLOSED
            self.order.save(update_fields=["status", "updated_at"])
        return self
