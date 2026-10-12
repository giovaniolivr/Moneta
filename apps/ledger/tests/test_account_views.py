from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.ledger.models import Account, AccountKind
from apps.ledger.services import create_account, ensure_default_account

pytestmark = pytest.mark.django_db
FRAGMENT = {"X-Moneta-Fragment": "1"}


@pytest.fixture
def logged(client: Client, user: Any) -> Client:
    client.force_login(user)
    return client


def test_list_shows_balances_and_totals(logged: Client, user: Any) -> None:
    ensure_default_account(user)
    create_account(user, name="Cartão", kind=AccountKind.CREDIT_CARD, opening_balance_cents=-30_000)
    html = logged.get(reverse("ledger:account_list")).content.decode()
    assert "Principal" in html
    assert "Cartão" in html
    assert "-R$\xa0300,00" in html


def test_create_credit_card_with_open_bill(logged: Client, user: Any) -> None:
    response = logged.post(
        reverse("ledger:account_create"),
        {
            "name": "Cartão Nubank",
            "kind": "credit_card",
            "opening_balance": "1.500,00",
            "opening_date": "2026-10-01",
        },
        headers=FRAGMENT,
    )
    assert response.status_code == 204
    card = Account.objects.for_user(user).get(name="Cartão Nubank")
    assert card.opening_balance_cents == -150_000  # fatura em aberto = dívida
    edit = logged.get(reverse("ledger:account_update", args=[card.pk]), headers=FRAGMENT)
    assert 'value="1.500,00"' in edit.content.decode()


def test_update_cannot_change_kind(logged: Client, user: Any) -> None:
    account = ensure_default_account(user)
    logged.post(
        reverse("ledger:account_update", args=[account.pk]),
        {
            "name": "Nubank",
            "kind": "credit_card",
            "opening_balance": "100",
            "opening_date": "2026-10-01",
        },
        headers=FRAGMENT,
    )
    account.refresh_from_db()
    assert (account.name, account.kind, account.opening_balance_cents) == (
        "Nubank",
        AccountKind.CHECKING,
        10_000,
    )


def test_duplicate_name_error(logged: Client, user: Any) -> None:
    ensure_default_account(user)
    response = logged.post(
        reverse("ledger:account_create"),
        {"name": "principal", "kind": "cash", "opening_date": "2026-10-01"},
        headers=FRAGMENT,
    )
    assert "Já existe uma conta" in response.content.decode()


def test_last_active_account_cannot_be_archived(logged: Client, user: Any) -> None:
    account = ensure_default_account(user)
    response = logged.post(reverse("ledger:account_archive", args=[account.pk]), headers=FRAGMENT)
    assert response.status_code == 200
    assert "ao menos uma conta ativa" in response.content.decode()
    account.refresh_from_db()
    assert not account.is_archived


def test_archive_and_delete(logged: Client, user: Any) -> None:
    ensure_default_account(user)
    spare = create_account(user, name="Carteira", kind=AccountKind.CASH)
    logged.post(reverse("ledger:account_archive", args=[spare.pk]), headers=FRAGMENT)
    spare.refresh_from_db()
    assert spare.is_archived
    assert (
        logged.post(reverse("ledger:account_delete", args=[spare.pk]), headers=FRAGMENT).status_code
        == 204
    )
    assert not Account.objects.filter(pk=spare.pk).exists()


@pytest.mark.parametrize(
    "name", ["ledger:account_update", "ledger:account_archive", "ledger:account_delete"]
)
def test_other_users_accounts_are_404(
    client: Client, user: Any, other_user: Any, name: str
) -> None:
    account = ensure_default_account(user)
    client.force_login(other_user)
    assert client.post(reverse(name, args=[account.pk])).status_code == 404
