from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.utils.models import BaseModel


class Merchant(BaseModel):
    name = models.CharField(_("Название"), max_length=255)
    inn = models.CharField(_("ИНН"), max_length=14, unique=True)
    commission_percent = models.DecimalField(_("Комиссия, %"), max_digits=5, decimal_places=2, default=0)
    is_active = models.BooleanField(_("Активен"), default=True)

    class Meta(BaseModel.Meta):
        verbose_name = _("Мерчант")
        verbose_name_plural = _("Мерчанты")

    def __str__(self):
        return self.name
