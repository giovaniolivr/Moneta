import datetime
from typing import Any

import pytest
from django.core.exceptions import ValidationError

from apps.ledger.models import Account, AccountKind
from apps.ledger.selectors import list_accounts
from apps.ledger.services import (
    DEFAULT_ACCOUNT_NAME,
    create_account,
    delete_account,
    ensure_default_account,
    set_account_archived,
    update_account,
)

pytestmark = pytest.mark.django_db


class TestDefaultAccount:
    def test_new_user_gets_default_account(self, user: Any) -> None:
        accounts = list(list_accounts(user))
        assert [(a.name, a.kind) for a in accounts] == [
            (DEFAULT_ACCOUNT_NAME, AccountKind.CHECKING)
        ]
        assert accounts[0].currency == "BRL"
        assert accounts[0].opening_balance_cents == 0

    def test_ensure_is_idempotent(self, user: Any) -> None:
        first = ensure_default_account(user)
        assert ensure_default_account(user) == first
        assert Account.objects.for_user(user).count() == 1

    def test_cannot_archive_or_delete_last_active_account(self, user: Any) -> None:
        account = ensure_default_account(user)
        with pytest.raises(ValidationError, match="ao menos uma conta ativa"):
            set_account_archived(account, archived=True)
        with pytest.raises(ValidationError, match="ao menos uma conta ativa"):
            delete_account(account)
        assert Account.objects.for_user(user).filter(is_archived=False).count() == 1

    def test_ensure_prefers_active_account(self, user: Any) -> None:
        main = ensure_default_account(user)
        create_account(user, name="Caixinha", kind=AccountKind.SAVINGS)
        set_account_archived(main, archived=True)
        assert ensure_default_account(user).name == "Caixinha"

    def test_delete_only_without_transactions(self, user: Any) -> None:
        ensure_default_account(user)
        spare = create_account(user, name="Sem uso", kind=AccountKind.CASH)
        delete_account(spare)
        assert not Account.objects.filter(pk=spare.pk).exists()


class TestCreateAccount:
    def test_credit_card_is_liability_with_negative_opening(self, user: Any) -> None:
        card = create_account(
            user,
            name="Cartão Nubank",
            kind=AccountKind.CREDIT_CARD,
            opening_balance_cents=-150_000,
            opening_date=datetime.date(2026, 10, 1),
        )
        assert card.is_liability
        assert card.opening_balance_cents == -150_000
        assert not ensure_default_account(user).is_liability

    @pytest.mark.parametrize("name", ["principal", "PRINCIPAL", "  Principal  "])
    def test_duplicate_name_rejected(self, user: Any, name: str) -> None:
        with pytest.raises(ValidationError, match="Já existe uma conta"):
            create_account(user, name=name, kind=AccountKind.SAVINGS)

    def test_invalid_kind_rejected(self, user: Any) -> None:
        with pytest.raises(ValidationError):
            create_account(user, name="X", kind="bitcoin")

    def test_blank_name_rejected(self, user: Any) -> None:
        with pytest.raises(ValidationError):
            create_account(user, name="  ", kind=AccountKind.CASH)


class TestUpdateAccount:
    def test_update_name_and_opening_balance(self, user: Any) -> None:
        account = ensure_default_account(user)
        update_account(account, name="Nubank", opening_balance_cents=12_345)
        account.refresh_from_db()
        assert (account.name, account.opening_balance_cents) == ("Nubank", 12_345)

    def test_archive_hides_from_listing(self, user: Any) -> None:
        account = ensure_default_account(user)
        create_account(user, name="Caixinha", kind=AccountKind.SAVINGS)
        set_account_archived(account, archived=True)
        assert account not in list_accounts(user)
        assert account in list_accounts(user, include_archived=True)


class TestIsolation:
    def test_users_only_see_their_accounts(self, user: Any, other_user: Any) -> None:
        mine = ensure_default_account(user)
        theirs = ensure_default_account(other_user)
        assert list(list_accounts(user)) == [mine]
        assert list(list_accounts(other_user)) == [theirs]

    def test_ids_are_uuid7(self, user: Any) -> None:
        assert ensure_default_account(user).id.version == 7
