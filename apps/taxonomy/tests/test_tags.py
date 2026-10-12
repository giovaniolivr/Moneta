from typing import Any

import pytest
from django.core.exceptions import ValidationError

from apps.taxonomy.models import Tag, TagAlias
from apps.taxonomy.selectors import list_tags, resolve_tag, search_tags
from apps.taxonomy.services import (
    add_tag_alias,
    delete_tag,
    get_or_create_tag,
    get_or_create_tags,
    merge_tags,
    remove_tag_alias,
    rename_tag,
)

pytestmark = pytest.mark.django_db


class TestGetOrCreate:
    def test_creates_with_clean_display_name(self, user: Any) -> None:
        tag = get_or_create_tag(user, "  Mercado  ")
        assert (tag.name, tag.normalized_name) == ("Mercado", "mercado")

    @pytest.mark.parametrize("variant", ["mercado", "MERCADO", " Mercado ", "Mércado"])
    def test_spelling_variants_resolve_to_same_tag(self, user: Any, variant: str) -> None:
        original = get_or_create_tag(user, "Mercado")
        assert get_or_create_tag(user, variant) == original
        assert Tag.objects.for_user(user).count() == 1

    def test_keeps_first_display_name(self, user: Any) -> None:
        get_or_create_tag(user, "mercado")
        assert get_or_create_tag(user, "MERCADO").name == "mercado"

    def test_semantically_similar_names_stay_separate(self, user: Any) -> None:
        assert get_or_create_tag(user, "Mercado") != get_or_create_tag(user, "Supermercado")

    @pytest.mark.parametrize("name", ["", "   "])
    def test_blank_rejected(self, user: Any, name: str) -> None:
        with pytest.raises(ValidationError):
            get_or_create_tag(user, name)

    def test_too_long_rejected(self, user: Any) -> None:
        with pytest.raises(ValidationError):
            get_or_create_tag(user, "x" * 61)

    def test_many_dedupes_and_skips_blanks(self, user: Any) -> None:
        tags = get_or_create_tags(user, ["Uber", "", "uber", "Delivery", "  ", "UBER"])
        assert [t.name for t in tags] == ["Uber", "Delivery"]


class TestAliases:
    def test_alias_resolves_to_tag(self, user: Any) -> None:
        mercado = get_or_create_tag(user, "Mercado")
        add_tag_alias(mercado, "Supermercado")
        assert resolve_tag(user, "SUPERMERCADO") == mercado
        assert get_or_create_tag(user, "supermercado") == mercado
        assert Tag.objects.for_user(user).count() == 1

    def test_alias_is_idempotent_for_same_tag(self, user: Any) -> None:
        mercado = get_or_create_tag(user, "Mercado")
        assert add_tag_alias(mercado, "super") == add_tag_alias(mercado, "SUPER")

    def test_alias_equal_to_own_name_rejected(self, user: Any) -> None:
        mercado = get_or_create_tag(user, "Mercado")
        with pytest.raises(ValidationError, match="igual ao nome"):
            add_tag_alias(mercado, "MERCADO")

    def test_alias_matching_existing_tag_suggests_merge(self, user: Any) -> None:
        mercado = get_or_create_tag(user, "Mercado")
        get_or_create_tag(user, "Supermercado")
        with pytest.raises(ValidationError, match="mesclar"):
            add_tag_alias(mercado, "supermercado")

    def test_alias_owned_by_other_tag_rejected(self, user: Any) -> None:
        mercado = get_or_create_tag(user, "Mercado")
        feira = get_or_create_tag(user, "Feira")
        add_tag_alias(mercado, "compras do mes")
        with pytest.raises(ValidationError, match="pertence"):
            add_tag_alias(feira, "Compras do mês")

    def test_remove_alias(self, user: Any) -> None:
        mercado = get_or_create_tag(user, "Mercado")
        remove_tag_alias(add_tag_alias(mercado, "super"))
        assert resolve_tag(user, "super") is None


class TestRename:
    def test_rename_changes_display_and_key(self, user: Any) -> None:
        tag = rename_tag(get_or_create_tag(user, "mercado"), "Mercado do bairro")
        assert (tag.name, tag.normalized_name) == ("Mercado do bairro", "mercado do bairro")
        assert resolve_tag(user, "mercado") is None  # nome antigo não vira apelido sozinho

    def test_rename_to_existing_tag_rejected(self, user: Any) -> None:
        get_or_create_tag(user, "Uber")
        with pytest.raises(ValidationError):
            rename_tag(get_or_create_tag(user, "Taxi"), "UBER")

    def test_rename_to_alias_of_other_tag_rejected(self, user: Any) -> None:
        add_tag_alias(get_or_create_tag(user, "Mercado"), "super")
        with pytest.raises(ValidationError, match="apelido"):
            rename_tag(get_or_create_tag(user, "Feira"), "Super")

    def test_rename_to_own_alias_promotes_it(self, user: Any) -> None:
        mercado = get_or_create_tag(user, "Mercado")
        add_tag_alias(mercado, "Supermercado")
        rename_tag(mercado, "Supermercado")
        assert not TagAlias.objects.filter(tag=mercado).exists()
        assert resolve_tag(user, "supermercado") == mercado


class TestMerge:
    def test_merge_turns_source_name_into_alias(self, user: Any) -> None:
        mercado = get_or_create_tag(user, "Mercado")
        supermercado = get_or_create_tag(user, "Supermercado")
        merge_tags(supermercado, mercado)
        assert not Tag.objects.filter(pk=supermercado.pk).exists()
        assert get_or_create_tag(user, "SUPERMERCADO") == mercado

    def test_merge_carries_source_aliases(self, user: Any) -> None:
        mercado = get_or_create_tag(user, "Mercado")
        supermercado = get_or_create_tag(user, "Supermercado")
        add_tag_alias(supermercado, "Atacadão")
        merge_tags(supermercado, mercado)
        assert resolve_tag(user, "atacadao") == mercado
        assert set(mercado.aliases.values_list("normalized_alias", flat=True)) == {
            "supermercado",
            "atacadao",
        }

    def test_merge_same_tag_rejected(self, user: Any) -> None:
        tag = get_or_create_tag(user, "Mercado")
        with pytest.raises(ValidationError):
            merge_tags(tag, tag)

    def test_merge_across_users_rejected(self, user: Any, other_user: Any) -> None:
        with pytest.raises(ValidationError):
            merge_tags(get_or_create_tag(user, "A"), get_or_create_tag(other_user, "B"))

    def test_delete_removes_aliases(self, user: Any) -> None:
        tag = get_or_create_tag(user, "Mercado")
        add_tag_alias(tag, "super")
        delete_tag(tag)
        assert not TagAlias.objects.for_user(user).exists()


class TestSearch:
    def test_prefix_matches_come_first_and_aliases_count(self, user: Any) -> None:
        for name in ["Supermercado", "Mercado", "Uber", "Mercadinho"]:
            get_or_create_tag(user, name)
        add_tag_alias(get_or_create_tag(user, "Uber"), "mercado livre entregas")
        names = [t.name for t in search_tags(user, "MERC")]
        assert names[:2] == ["Mercadinho", "Mercado"]
        assert set(names[2:]) == {"Supermercado", "Uber"}

    def test_blank_query_lists_all(self, user: Any) -> None:
        get_or_create_tags(user, ["A", "B"])
        assert len(search_tags(user, " ")) == 2


class TestIsolation:
    def test_same_name_is_independent_per_user(self, user: Any, other_user: Any) -> None:
        mine = get_or_create_tag(user, "Mercado")
        theirs = get_or_create_tag(other_user, "Mercado")
        assert mine != theirs
        assert list(list_tags(user)) == [mine]

    def test_aliases_do_not_leak_between_users(self, user: Any, other_user: Any) -> None:
        add_tag_alias(get_or_create_tag(user, "Mercado"), "super")
        assert resolve_tag(other_user, "super") is None
        assert search_tags(other_user, "super") == []
        # O outro usuário pode usar o mesmo texto como tag própria.
        assert get_or_create_tag(other_user, "Super").owner == other_user
