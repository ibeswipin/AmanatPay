from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from celery_tasks.tasks import sync_draft_order_to_radius_task

from .models import DraftOrder


@receiver(post_save, sender=DraftOrder)
def draft_order_created(sender, instance, created, **kwargs):
    """
    DraftOrder yaratilganda RadiusCRM ga sinxronlash uchun celery task ishga tushadi.
    transaction.on_commit orqali DB commit bo'lgandan keyin ishga tushadi.
    """
    if created:
        if settings.IS_PRODUCTION:
            transaction.on_commit(
                lambda: sync_draft_order_to_radius_task.delay(instance.id)
            )
