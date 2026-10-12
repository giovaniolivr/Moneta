from typing import Any

import pytest
from django.core.exceptions import ValidationError

from apps.taxonomy.defaults import DEFAULT_CATEGORIES
from apps.taxonomy.models import Category, CategoryKind
from apps.taxonomy.selectors import list_categories
from apps.taxonomy.services import (
    create_category,
    seed_default_categories,
    set_category_archived,
    update_category,
)

pytestmark = pytest.mark.django_db


class TestSeed:
    def test_new_user_gets_default_categories(self, user: Any) -> None:
        assert list_categories(user).count() == len(DEFAULT_CATEGORIES)
        names = set(list_categories(user, kind=CategoryKind.EXPENSE).values_list("name", flat=True))
        assert {"Alimentação", "Transporte", "Outros"} <= names
        assert "Investimentos" not in names  # investir é transferência, não despesa

    def test_seed_is_idempotent(self, user: Any) -> None:
        assert seed_default_categories(user) == 0
        assert list_categories(user).count() == len(DEFAULT_CATEGORIES)

    def test_seed_fills_only_missing(self, user: Any) -> None:
        Category.objects.for_user(user).filter(name="Lazer").delete()
        assert seed_default_categories(user) == 1


class TestCreateCategory:
    def test_create(self, user: Any) -> None:
        cat = create_category(user, name="  Pets ", kind=CategoryKind.EXPENSE, color="3")
        assert cat.name == "Pets"
        assert cat.normalized_name == "pets"
        assert cat.owner == user

    @pytest.mark.parametrize("name", ["alimentação", "ALIMENTACAO", " Alimentacao "])
    def test_duplicate_name_ignores_case_accents_spaces(self, user: Any, name: str) -> None:
        with pytest.raises(ValidationError, match="Já existe uma categoria"):
            create_category(user, name=name, kind=CategoryKind.EXPENSE)

    def test_same_name_allowed_for_other_kind(self, user: Any) -> None:
        create_category(user, name="Alimentação", kind=CategoryKind.INCOME)

    @pytest.mark.parametrize("name", ["", "   "])
    def test_blank_name_rejected(self, user: Any, name: str) -> None:
        with pytest.raises(ValidationError) as exc:
            create_category(user, name=name, kind=CategoryKind.EXPENSE)
        assert "name" in exc.value.message_dict

    def test_invalid_kind_and_color_rejected(self, user: Any) -> None:
        with pytest.raises(ValidationError):
            create_category(user, name="X", kind="transfer")
        with pytest.raises(ValidationError):
            create_category(user, name="X", kind=CategoryKind.EXPENSE, color="99")


class TestUpdateCategory:
    def test_rename(self, user: Any) -> None:
        cat = Category.objects.for_user(user).get(name="Lazer")
        update_category(cat, name="Lazer e cultura", icon="ticket")
        cat.refresh_from_db()
        assert (cat.name, cat.normalized_name, cat.icon) == (
            "Lazer e cultura",
            "lazer e cultura",
            "ticket",
        )

    def test_rename_to_existing_name_rejected(self, user: Any) -> None:
        cat = Category.objects.for_user(user).get(name="Lazer")
        with pytest.raises(ValidationError):
            update_category(cat, name="compras")

    def test_archive_hides_from_listing(self, user: Any) -> None:
        cat = Category.objects.for_user(user).get(name="Lazer")
        set_category_archived(cat, archived=True)
        assert cat not in list_categories(user)
        assert cat in list_categories(user, include_archived=True)


class TestIsolation:
    def test_users_only_see_their_categories(self, user: Any, other_user: Any) -> None:
        mine = create_category(user, name="Pets", kind=CategoryKind.EXPENSE)
        assert mine in list_categories(user)
        assert mine not in list_categories(other_user)
        assert not list_categories(other_user).filter(owner=user).exists()

    def test_same_name_allowed_for_different_users(self, user: Any, other_user: Any) -> None:
        create_category(user, name="Pets", kind=CategoryKind.EXPENSE)
        create_category(other_user, name="Pets", kind=CategoryKind.EXPENSE)

    def test_anonymous_sees_nothing(self, user: Any) -> None:
        from django.contrib.auth.models import AnonymousUser

        assert list_categories(AnonymousUser()).count() == 0
