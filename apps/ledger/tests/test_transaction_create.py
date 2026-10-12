import datetime
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.ledger.models import Account, AccountKind, PaymentMethod, Transaction, TransactionKind
from apps.ledger.selectors import categories_by_usage, frequent_tags, last_used_account
from apps.ledger.services import (
    TransactionData,
    create_account,
    create_transaction,
    ensure_default_account,
)
from apps.taxonomy.models import Category

pytestmark = pytest.mark.django_db

URL = "ledger:transaction_create"
FRAGMENT = {"X-Moneta-Fragment": "1"}


@pytest.fixture
def logged(client: Client, user: Any) -> Client:
    client.force_login(user)
    return client


@pytest.fixture
def account(user: Any) -> Account:
    return ensure_default_account(user)


def category(user: Any, name: str) -> Category:
    return Category.objects.for_user(user).get(name=name)


def post_data(**overrides: Any) -> dict[str, Any]:
    return {"kind": "expense", "date": timezone.localdate().isoformat(), **overrides}


class TestRender:
    def test_full_page(self, logged: Client, account: Account) -> None:
        html = logged.get(reverse(URL)).content.decode()
        assert "<html" in html
        assert 'data-transaction-form data-kind="expense"' in html
        assert 'inputmode="decimal"' in html
        assert f'<option value="{account.pk}" selected>' in html

    def test_fragment_for_drawer(self, logged: Client, account: Account) -> None:
        html = logged.get(reverse(URL), headers=FRAGMENT).content.decode()
        assert "<html" not in html
        assert "data-transaction-form" in html


class TestCreate:
    def test_expense_redirects_home(self, logged: Client, user: Any, account: Account) -> None:
        food = category(user, "Alimentação")
        response = logged.post(
            reverse(URL),
            post_data(
                amount="45,90",
                category=food.pk,
                account=account.pk,
                tags="Restaurante, almoço, , restaurante",
                description="Almoço",
            ),
        )
        assert response.status_code == 302
        assert response["Location"] == reverse("home")
        tx = Transaction.objects.for_user(user).get()
        assert (tx.amount_cents, tx.category, tx.description) == (4590, food, "Almoço")
        assert {t.name for t in tx.tags.all()} == {"almoço", "Restaurante"}
        home = logged.get(reverse("home")).content.decode()
        assert "Despesa de R$\xa045,90 registrada." in home

    def test_fragment_save_returns_204_event(
        self, logged: Client, user: Any, account: Account
    ) -> None:
        response = logged.post(
            reverse(URL),
            post_data(amount="10", category=category(user, "Lazer").pk, account=account.pk),
            headers=FRAGMENT,
        )
        assert response.status_code == 204
        assert response["X-Moneta-Event"] == "done"

    def test_save_and_new_keeps_kind_and_account(
        self, logged: Client, user: Any, account: Account
    ) -> None:
        salary = category(user, "Salário")
        response = logged.post(
            reverse(URL),
            post_data(
                kind="income",
                amount="5.000",
                category=salary.pk,
                account=account.pk,
                save_and_new="1",
            ),
            headers=FRAGMENT,
        )
        html = response.content.decode()
        assert response.status_code == 200
        assert "Receita de R$\xa05.000,00 registrada." in html
        assert 'data-kind="income"' in html
        assert 'value="5.000"' not in html  # valor limpo para o próximo lançamento
        assert Transaction.objects.for_user(user).count() == 1

    def test_transfer_ignores_hidden_category(
        self, logged: Client, user: Any, account: Account
    ) -> None:
        savings = create_account(user, name="Caixinha", kind=AccountKind.SAVINGS)
        logged.post(
            reverse(URL),
            post_data(
                kind="transfer",
                amount="100",
                category=category(user, "Lazer").pk,  # sobrou marcado na tela
                payment_method="pix",
                account=account.pk,
                destination_account=savings.pk,
            ),
        )
        tx = Transaction.objects.for_user(user).get()
        assert (tx.kind, tx.category, tx.destination_account) == (
            TransactionKind.TRANSFER,
            None,
            savings,
        )
        assert tx.payment_method == ""

    def test_credit_card_defaults_payment_method(self, logged: Client, user: Any) -> None:
        card = create_account(user, name="Cartão", kind=AccountKind.CREDIT_CARD)
        logged.post(
            reverse(URL),
            post_data(amount="30", category=category(user, "Compras").pk, account=card.pk),
        )
        assert Transaction.objects.for_user(user).get().payment_method == PaymentMethod.CREDIT


class TestErrors:
    def _post(self, client: Client, **data: Any) -> str:
        response = client.post(reverse(URL), post_data(**data), headers=FRAGMENT)
        assert response.status_code == 200
        return response.content.decode()

    def test_invalid_amount(self, logged: Client, user: Any, account: Account) -> None:
        html = self._post(logged, amount="12,345", category="", account=account.pk)
        assert "Valor inválido" in html
        assert not Transaction.objects.exists()

    def test_category_required_for_expense(
        self, logged: Client, user: Any, account: Account
    ) -> None:
        html = self._post(logged, amount="10", account=account.pk)
        assert "Escolha uma categoria." in html  # erro de regra do model, no campo certo
        assert not Transaction.objects.exists()

    def test_all_errors_shown_at_once(self, logged: Client, account: Account) -> None:
        html = self._post(logged, amount="", account=account.pk)
        assert "Este campo é obrigatório." in html
        assert "Escolha uma categoria." in html

    def test_transfer_requires_destination(self, logged: Client, account: Account) -> None:
        html = self._post(logged, kind="transfer", amount="10", account=account.pk)
        assert "Informe a conta de destino." in html

    def test_income_category_on_expense(self, logged: Client, user: Any, account: Account) -> None:
        html = self._post(
            logged, amount="10", category=category(user, "Salário").pk, account=account.pk
        )
        assert "é de receita" in html

    def test_future_date(self, logged: Client, user: Any, account: Account) -> None:
        tomorrow = timezone.localdate() + datetime.timedelta(days=1)
        html = self._post(
            logged,
            amount="10",
            category=category(user, "Lazer").pk,
            account=account.pk,
            date=tomorrow.isoformat(),
        )
        assert "Movimentações futuras" in html

    def test_other_users_ids_are_invalid_choices(
        self, logged: Client, user: Any, other_user: Any, account: Account
    ) -> None:
        their_account = ensure_default_account(other_user)
        their_category = category(other_user, "Lazer")
        html = self._post(logged, amount="10", category=their_category.pk, account=their_account.pk)
        assert html.count("Faça uma escolha válida") == 2
        assert not Transaction.objects.exists()


class TestShortcuts:
    def test_categories_ordered_by_recent_usage(self, user: Any, account: Account) -> None:
        transport = category(user, "Transporte")
        for _ in range(2):
            create_transaction(
                user,
                TransactionData(
                    kind="expense",
                    amount_cents=100,
                    date=timezone.localdate(),
                    account=account,
                    category=transport,
                    tags=["Uber"],
                ),
            )
        names = [c.name for c in categories_by_usage(user, "expense")]
        assert names[0] == "Transporte"
        assert names[1:] == sorted(names[1:])
        assert [t.name for t in frequent_tags(user)] == ["Uber"]

    def test_last_used_account(self, user: Any, account: Account) -> None:
        assert last_used_account(user) == account
        card = create_account(user, name="Cartão", kind=AccountKind.CREDIT_CARD)
        create_transaction(
            user,
            TransactionData(
                kind="expense",
                amount_cents=100,
                date=timezone.localdate(),
                account=card,
                category=category(user, "Lazer"),
            ),
        )
        assert last_used_account(user) == card
