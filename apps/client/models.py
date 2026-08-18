from decimal import Decimal

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.utils.models import BaseModel


class Client(BaseModel):
    full_name = models.CharField(_("ФИО"), max_length=255)
    phone = models.CharField(_("Телефон"), max_length=20, unique=True)
    passport = models.CharField(_("Паспорт"), max_length=20, blank=True)
    limit = models.DecimalField(_("Лимит рассрочки"), max_digits=12, decimal_places=2, default=0)

    class Meta(BaseModel.Meta):
        verbose_name = _("Клиент")
        verbose_name_plural = _("Клиенты")

    def __str__(self):
        return f"{self.full_name} ({self.phone})"

    @property
    def debt(self) -> Decimal:
        """Остаток долга по активным заказам."""
        return sum((order.remainder for order in self.orders.filter(status="active")), Decimal("0"))

    @property
    def available_limit(self) -> Decimal:
        return Decimal(self.limit) - self.debt
