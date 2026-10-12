from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.db.models import QuerySet

from .models import Category, CategoryKind


def list_categories(
    user: AbstractBaseUser | AnonymousUser,
    *,
    kind: CategoryKind | str | None = None,
    include_archived: bool = False,
) -> QuerySet[Category]:
    qs = Category.objects.for_user(user)
    if kind is not None:
        qs = qs.filter(kind=kind)
    if not include_archived:
        qs = qs.filter(is_archived=False)
    return qs
