from typing import Any

from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from .services import seed_default_categories


@receiver(post_save, sender=settings.AUTH_USER_MODEL, dispatch_uid="taxonomy_seed_new_user")
def seed_new_user(sender: Any, instance: Any, created: bool, raw: bool = False, **_: Any) -> None:
    if created and not raw:
        seed_default_categories(instance)
