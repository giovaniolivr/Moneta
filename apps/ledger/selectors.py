from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.db.models import QuerySet

from .models import Account


def list_accounts(
    user: AbstractBaseUser | AnonymousUser, *, include_archived: bool = False
) -> QuerySet[Account]:
    qs = Account.objects.for_user(user)
    if not include_archived:
        qs = qs.filter(is_archived=False)
    return qs
