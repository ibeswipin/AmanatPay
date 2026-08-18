import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _


class BaseModel(models.Model):
    """UUID + метки времени. Базовый класс всех доменных моделей."""

    uuid = models.UUIDField(_("UUID"), default=uuid.uuid4, unique=True, editable=False, db_index=True)
    created_at = models.DateTimeField(_("Создан"), auto_now_add=True)
    updated_at = models.DateTimeField(_("Обновлён"), auto_now=True)

    class Meta:
        abstract = True
        ordering = ["-created_at"]
