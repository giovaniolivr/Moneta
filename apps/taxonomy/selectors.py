from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.db.models import Case, Count, IntegerField, Q, QuerySet, Value, When

from apps.core.names import InvalidNameError, normalize_name

from .models import Category, CategoryKind, Tag


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


def list_tags(user: AbstractBaseUser | AnonymousUser) -> QuerySet[Tag]:
    return Tag.objects.for_user(user).prefetch_related("aliases")


def resolve_tag(user: AbstractBaseUser | AnonymousUser, text: str) -> Tag | None:
    """Tag que corresponde a `text` pelo nome normalizado ou por um apelido; None se não houver."""
    try:
        key = normalize_name(text)
    except InvalidNameError:
        return None
    qs = Tag.objects.for_user(user)
    return (
        qs.filter(normalized_name=key).first() or qs.filter(aliases__normalized_alias=key).first()
    )


def search_tags(
    user: AbstractBaseUser | AnonymousUser, query: str, *, limit: int = 10
) -> list[Tag]:
    """Autocomplete: casa nome ou apelido em qualquer posição; prefixos primeiro."""
    try:
        key = normalize_name(query)
    except InvalidNameError:
        return list(list_tags(user)[:limit])
    qs = (
        Tag.objects.for_user(user)
        .filter(Q(normalized_name__contains=key) | Q(aliases__normalized_alias__contains=key))
        .annotate(
            rank=Case(
                When(normalized_name__startswith=key, then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            )
        )
        .order_by("rank", "normalized_name")
        .distinct()
    )
    return list(qs[:limit])


def categories_with_usage(user: AbstractBaseUser | AnonymousUser) -> QuerySet[Category]:
    """Todas as categorias (inclusive arquivadas) com o total de movimentações de cada uma."""
    return list_categories(user, include_archived=True).annotate(uses=Count("transactions"))


def tags_with_usage(user: AbstractBaseUser | AnonymousUser) -> QuerySet[Tag]:
    return list_tags(user).annotate(uses=Count("transactions")).order_by("-uses", "normalized_name")
