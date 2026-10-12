from typing import Any

from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from .services import ensure_default_account


@receiver(post_save, sender=settings.AUTH_USER_MODEL, dispatch_uid="ledger_seed_new_user")
def create_default_account(
    sender: Any, instance: Any, created: bool, raw: bool = False, **_: Any
) -> None:
    if created and not raw:
        ensure_default_account(instance)
