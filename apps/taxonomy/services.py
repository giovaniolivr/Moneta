from django.db import transaction

from apps.core.names import normalize_name
from apps.users.models import User

from .defaults import DEFAULT_CATEGORIES
from .models import Category, CategoryKind, ColorSlot


def _validated_save(category: Category) -> Category:
    category.full_clean()
    category.save()
    return category


def create_category(
    user: User,
    *,
    name: str,
    kind: CategoryKind | str,
    color: ColorSlot | str = ColorSlot.C1,
    icon: str = "",
) -> Category:
    return _validated_save(Category(owner=user, name=name, kind=kind, color=color, icon=icon))


def update_category(
    category: Category,
    *,
    name: str | None = None,
    color: ColorSlot | str | None = None,
    icon: str | None = None,
) -> Category:
    """O tipo (despesa/receita) não muda: mudaria o sentido de movimentações já registradas."""
    if name is not None:
        category.name = name
    if color is not None:
        category.color = color
    if icon is not None:
        category.icon = icon
    return _validated_save(category)


def set_category_archived(category: Category, *, archived: bool) -> Category:
    """Arquivar esconde a categoria dos formulários, mas preserva o histórico."""
    category.is_archived = archived
    category.save(update_fields=["is_archived", "updated_at"])
    return category


@transaction.atomic
def seed_default_categories(user: User) -> int:
    """Cria as categorias padrão que o usuário ainda não tem. Idempotente; retorna quantas criou."""
    existing = set(
        Category.objects.for_user(user).values_list("kind", "normalized_name"),
    )
    new = [
        Category(owner=user, name=d.name, kind=d.kind, color=d.color, icon=d.icon)
        for d in DEFAULT_CATEGORIES
        if (d.kind, normalize_name(d.name)) not in existing
    ]
    for category in new:
        category.normalize()
    Category.objects.bulk_create(new)
    return len(new)
