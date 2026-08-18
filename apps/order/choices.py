from django.db import models
from django.utils.translation import gettext_lazy as _


class OrderStatus(models.TextChoices):
    NEW = "new", _("Новый")
    ACTIVE = "active", _("Рассрочка активна")
    CLOSED = "closed", _("Погашен")
    CANCELED = "canceled", _("Отменён")
