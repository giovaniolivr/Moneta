from collections.abc import Iterable
from typing import Any

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import RestrictedError

from apps.core.names import InvalidNameError, clean_display_name, normalize_name
from apps.users.models import User

from .defaults import DEFAULT_CATEGORIES
from .models import Category, CategoryKind, ColorSlot, Tag, TagAlias
from .selectors import resolve_tag


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


# Tags -------------------------------------------------------------------------


def _clean_name_or_error(name: str) -> str:
    try:
        return clean_display_name(name)
    except InvalidNameError as exc:
        raise ValidationError({"name": str(exc)}) from exc


def get_or_create_tag(user: User, name: str) -> Tag:
    """Devolve a tag equivalente a `name` (por nome ou apelido) ou cria uma nova."""
    display = _clean_name_or_error(name)
    if (existing := resolve_tag(user, display)) is not None:
        return existing
    tag = Tag(owner=user, name=display)
    tag.full_clean()
    try:
        with transaction.atomic():
            tag.save()
    except IntegrityError:
        # Criada em paralelo por outra requisição entre a busca e o insert.
        if (existing := resolve_tag(user, display)) is not None:
            return existing
        raise
    return tag


def get_or_create_tags(user: User, names: Iterable[str]) -> list[Tag]:
    """Resolve vários nomes; ignora vazios e remove duplicatas, preservando a ordem."""
    tags: dict[object, Tag] = {}
    for name in names:
        if name.strip():
            tag = get_or_create_tag(user, name)
            tags.setdefault(tag.pk, tag)
    return list(tags.values())


@transaction.atomic
def rename_tag(tag: Tag, name: str) -> Tag:
    """Muda o nome de exibição.

    Não cria apelido para o nome antigo: equivalências nunca são implícitas.
    """
    tag.name = _clean_name_or_error(name)
    key = normalize_name(tag.name)
    alias = TagAlias.objects.filter(owner_id=tag.owner_id, normalized_alias=key).first()
    if alias is not None:
        if alias.tag_id != tag.pk:
            raise ValidationError({"name": f"Este nome é apelido da tag “{alias.tag}”."})
        alias.delete()  # o apelido virou o próprio nome
    tag.full_clean()
    tag.save()
    return tag


def add_tag_alias(tag: Tag, alias: str) -> TagAlias:
    """Faz `alias` resolver para `tag`. Se já existe uma tag com esse nome, o caminho é mesclar."""
    key = normalize_name(_clean_name_or_error(alias))
    if key == tag.normalized_name:
        raise ValidationError({"alias": "O apelido é igual ao nome da tag."})
    clash = Tag.objects.filter(owner_id=tag.owner_id, normalized_name=key).first()
    if clash is not None:
        raise ValidationError({"alias": f"Já existe a tag “{clash}”. Use mesclar tags."})
    existing = TagAlias.objects.filter(owner_id=tag.owner_id, normalized_alias=key).first()
    if existing is not None:
        if existing.tag_id == tag.pk:
            return existing
        raise ValidationError({"alias": f"Este apelido já pertence à tag “{existing.tag}”."})
    return TagAlias.objects.create(owner_id=tag.owner_id, tag=tag, normalized_alias=key)


def remove_tag_alias(alias: TagAlias) -> None:
    alias.delete()


def _repoint_tag_relations(source: Tag, target: Tag) -> None:
    """Move todas as associações M2M de `source` para `target`, sem duplicar.

    Genérico: cobre qualquer modelo que venha a ter M2M com Tag (movimentações, objetivos...).
    """
    for rel in Tag._meta.related_objects:
        if not rel.many_to_many:
            continue
        through: Any = rel.through  # type: ignore[attr-defined]
        fk_fields = [f for f in through._meta.fields if f.is_relation]
        tag_fk = next(f.name for f in fk_fields if f.related_model is Tag)
        other_fk = next(f.name for f in fk_fields if f.related_model is not Tag)
        already_on_target = through.objects.filter(**{tag_fk: target}).values(other_fk)
        through.objects.filter(**{tag_fk: source, f"{other_fk}__in": already_on_target}).delete()
        through.objects.filter(**{tag_fk: source}).update(**{tag_fk: target})


@transaction.atomic
def merge_tags(source: Tag, target: Tag) -> Tag:
    """Funde `source` em `target` (ação explícita do usuário).

    Associações e apelidos passam para `target`; o nome de `source` vira apelido de `target`,
    então digitar o nome antigo no futuro resolve para a tag mesclada.
    """
    if source.owner_id != target.owner_id:
        raise ValidationError("Não é possível mesclar tags de usuários diferentes.")
    if source.pk == target.pk:
        raise ValidationError("Escolha duas tags diferentes para mesclar.")
    _repoint_tag_relations(source, target)
    TagAlias.objects.filter(tag=source).update(tag=target)
    source_key = source.normalized_name
    source.delete()
    TagAlias.objects.create(owner_id=target.owner_id, tag=target, normalized_alias=source_key)
    return target


def delete_tag(tag: Tag) -> None:
    """Remove a tag, suas associações e apelidos. As movimentações em si não são afetadas."""
    tag.delete()


def delete_category(category: Category) -> None:
    """Só categorias sem movimentações podem ser excluídas; as demais são arquivadas."""
    try:
        category.delete()
    except RestrictedError as exc:
        raise ValidationError(
            "Esta categoria tem movimentações. Arquive-a para escondê-la sem perder o histórico."
        ) from exc
