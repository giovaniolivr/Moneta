from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.db.models import QuerySet

from .models import Recurrence


def list_recurrences(user: AbstractBaseUser | AnonymousUser) -> QuerySet[Recurrence]:
    return (
        Recurrence.objects.for_user(user)
        .select_related("account", "destination_account", "category")
        .prefetch_related("tags")
    )
