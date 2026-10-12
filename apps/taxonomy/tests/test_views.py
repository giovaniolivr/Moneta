from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.ledger.models import Transaction
from apps.ledger.services import TransactionData, create_transaction, ensure_default_account
from apps.taxonomy.models import Category, Tag, TagAlias
from apps.taxonomy.services import add_tag_alias, get_or_create_tag

pytestmark = pytest.mark.django_db
FRAGMENT = {"X-Moneta-Fragment": "1"}


@pytest.fixture
def logged(client: Client, user: Any) -> Client:
    client.force_login(user)
    return client


def _category(user: Any, name: str) -> Category:
    return Category.objects.for_user(user).get(name=name)


def _spend(user: Any, category: Category, tags: list[str]) -> Transaction:
    return create_transaction(
        user,
        TransactionData(
            kind="expense",
            amount_cents=1_000,
            date=timezone.localdate(),
            account=ensure_default_account(user),
            category=category,
            tags=tags,
        ),
    )


class TestIndex:
    def test_lists_categories_and_tags_with_usage(self, logged: Client, user: Any) -> None:
        _spend(user, _category(user, "Alimentação"), ["Mercado"])
        html = logged.get(reverse("taxonomy:index")).content.decode()
        assert "Despesas" in html
        assert "Receitas" in html
        assert "1 movimentação" in html
        assert "#Mercado" in html


class TestCategories:
    def test_create(self, logged: Client, user: Any) -> None:
        response = logged.post(
            reverse("taxonomy:category_create"),
            {"name": "Pets", "kind": "expense", "color": "5", "icon": "paw-print"},
            headers=FRAGMENT,
        )
        assert response.status_code == 204
        pets = _category(user, "Pets")
        assert (pets.color, pets.icon) == ("5", "paw-print")

    def test_duplicate_name_shows_error(self, logged: Client) -> None:
        response = logged.post(
            reverse("taxonomy:category_create"),
            {"name": "alimentacao", "kind": "expense", "color": "1", "icon": "utensils"},
            headers=FRAGMENT,
        )
        assert response.status_code == 200
        assert "Já existe uma categoria" in response.content.decode()

    def test_unknown_icon_rejected(self, logged: Client) -> None:
        response = logged.post(
            reverse("taxonomy:category_create"),
            {"name": "X", "kind": "expense", "color": "1", "icon": "skull"},
            headers=FRAGMENT,
        )
        assert response.status_code == 200
        assert not Category.objects.filter(name="X").exists()

    def test_update_cannot_change_kind(self, logged: Client, user: Any) -> None:
        food = _category(user, "Alimentação")
        logged.post(
            reverse("taxonomy:category_update", args=[food.pk]),
            {"name": "Comida", "kind": "income", "color": "2", "icon": "pizza"},
            headers=FRAGMENT,
        )
        food.refresh_from_db()
        assert (food.name, food.kind, food.icon) == ("Comida", "expense", "pizza")

    def test_archive_toggle(self, logged: Client, user: Any) -> None:
        leisure = _category(user, "Lazer")
        url = reverse("taxonomy:category_archive", args=[leisure.pk])
        logged.post(url, headers=FRAGMENT)
        leisure.refresh_from_db()
        assert leisure.is_archived
        logged.post(url, headers=FRAGMENT)
        leisure.refresh_from_db()
        assert not leisure.is_archived

    def test_delete_unused_and_refuse_used(self, logged: Client, user: Any) -> None:
        unused, used = _category(user, "Educação"), _category(user, "Alimentação")
        _spend(user, used, [])
        assert (
            logged.post(
                reverse("taxonomy:category_delete", args=[unused.pk]), headers=FRAGMENT
            ).status_code
            == 204
        )
        response = logged.post(
            reverse("taxonomy:category_delete", args=[used.pk]), headers=FRAGMENT
        )
        assert response.status_code == 200
        assert "Arquive-a" in response.content.decode()
        assert Category.objects.filter(pk=used.pk).exists()


class TestTags:
    def test_rename_keeps_drawer_open(self, logged: Client, user: Any) -> None:
        tag = get_or_create_tag(user, "mercado")
        response = logged.post(
            reverse("taxonomy:tag_edit", args=[tag.pk]), {"name": "Mercado"}, headers=FRAGMENT
        )
        assert response.status_code == 200
        assert response["X-Moneta-Event"] == "changed"
        assert "Nome atualizado." in response.content.decode()
        tag.refresh_from_db()
        assert tag.name == "Mercado"

    def test_alias_add_conflict_and_remove(self, logged: Client, user: Any) -> None:
        mercado = get_or_create_tag(user, "Mercado")
        get_or_create_tag(user, "Feira")
        url = reverse("taxonomy:tag_alias_add", args=[mercado.pk])
        assert (
            "Apelido adicionado."
            in logged.post(url, {"alias": "Supermercado"}, headers=FRAGMENT).content.decode()
        )
        conflict = logged.post(url, {"alias": "feira"}, headers=FRAGMENT).content.decode()
        assert "Use mesclar tags" in conflict
        alias = TagAlias.objects.get(tag=mercado)
        logged.post(reverse("taxonomy:tag_alias_remove", args=[alias.pk]), headers=FRAGMENT)
        assert not TagAlias.objects.filter(tag=mercado).exists()

    def test_merge_moves_transactions(self, logged: Client, user: Any) -> None:
        tx = _spend(user, _category(user, "Alimentação"), ["Supermercado"])
        source = get_or_create_tag(user, "Supermercado")
        target = get_or_create_tag(user, "Mercado")
        response = logged.post(
            reverse("taxonomy:tag_merge", args=[source.pk]),
            {"target": target.pk},
            headers=FRAGMENT,
        )
        assert response.status_code == 204
        assert [t.name for t in tx.tags.all()] == ["Mercado"]

    def test_cannot_merge_into_itself(self, logged: Client, user: Any) -> None:
        tag = get_or_create_tag(user, "Mercado")
        response = logged.post(
            reverse("taxonomy:tag_merge", args=[tag.pk]), {"target": tag.pk}, headers=FRAGMENT
        )
        assert response.status_code == 200
        assert Tag.objects.filter(pk=tag.pk).exists()

    def test_delete(self, logged: Client, user: Any) -> None:
        tag = get_or_create_tag(user, "Mercado")
        logged.post(reverse("taxonomy:tag_delete", args=[tag.pk]), headers=FRAGMENT)
        assert not Tag.objects.filter(pk=tag.pk).exists()


class TestIsolation:
    def test_other_users_objects_are_404(self, client: Client, user: Any, other_user: Any) -> None:
        category = _category(user, "Lazer")
        tag = get_or_create_tag(user, "Mercado")
        alias = add_tag_alias(tag, "super")
        client.force_login(other_user)
        for name, pk in [
            ("taxonomy:category_update", category.pk),
            ("taxonomy:category_archive", category.pk),
            ("taxonomy:category_delete", category.pk),
            ("taxonomy:tag_edit", tag.pk),
            ("taxonomy:tag_alias_add", tag.pk),
            ("taxonomy:tag_merge", tag.pk),
            ("taxonomy:tag_delete", tag.pk),
            ("taxonomy:tag_alias_remove", alias.pk),
        ]:
            assert client.post(reverse(name, args=[pk])).status_code == 404, name
        assert Tag.objects.filter(pk=tag.pk).exists()
        assert TagAlias.objects.filter(pk=alias.pk).exists()

    def test_merge_target_must_be_own_tag(self, logged: Client, user: Any, other_user: Any) -> None:
        mine = get_or_create_tag(user, "Mercado")
        theirs = get_or_create_tag(other_user, "Feira")
        response = logged.post(
            reverse("taxonomy:tag_merge", args=[mine.pk]), {"target": theirs.pk}, headers=FRAGMENT
        )
        assert response.status_code == 200
        assert Tag.objects.filter(pk=mine.pk).exists()
