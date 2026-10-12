import datetime
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.ledger.models import Account, AccountKind, Transaction
from apps.ledger.services import (
    BatchError,
    TransactionData,
    create_account,
    create_transactions,
    ensure_default_account,
)
from apps.taxonomy.models import Category

pytestmark = pytest.mark.django_db
URL = "ledger:transaction_batch"
TODAY = datetime.date(2026, 10, 15)


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(timezone, "localdate", lambda *a, **k: TODAY)


@pytest.fixture
def logged(client: Client, user: Any) -> Client:
    client.force_login(user)
    return client


@pytest.fixture
def account(user: Any) -> Account:
    return ensure_default_account(user)


def cat(user: Any, name: str) -> Category:
    return Category.objects.for_user(user).get(name=name)


def batch_post(rows: list[dict[str, Any]], **header: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "linhas-TOTAL_FORMS": str(len(rows)),
        "linhas-INITIAL_FORMS": "0",
        "linhas-MIN_NUM_FORMS": "0",
        "linhas-MAX_NUM_FORMS": "100",
        "lote-date": header.get("date", TODAY.isoformat()),
        "lote-account": header["account"],
    }
    for i, row in enumerate(rows):
        data[f"linhas-{i}-kind"] = row.get("kind", "expense")
        for key, value in row.items():
            if key != "kind":
                data[f"linhas-{i}-{key}"] = value
    return data


def item(account: Account, category: Category, cents: int) -> TransactionData:
    return TransactionData(
        kind="expense", amount_cents=cents, date=TODAY, account=account, category=category
    )


class TestService:
    def test_all_or_nothing_with_every_error_reported(self, user: Any, account: Account) -> None:
        food, salary = cat(user, "Alimentação"), cat(user, "Salário")
        items = [
            TransactionData(
                kind="expense", amount_cents=100, date=TODAY, account=account, category=food
            ),
            TransactionData(
                kind="expense", amount_cents=100, date=TODAY, account=account, category=salary
            ),
            TransactionData(
                kind="expense", amount_cents=0, date=TODAY, account=account, category=food
            ),
        ]
        with pytest.raises(BatchError) as exc:
            create_transactions(user, items)
        assert set(exc.value.errors) == {1, 2}
        assert not Transaction.objects.exists()  # a linha 0, válida, também não foi gravada

    def test_success(self, user: Any, account: Account) -> None:
        food = cat(user, "Alimentação")
        items = [
            TransactionData(
                kind="expense", amount_cents=c, date=TODAY, account=account, category=food
            )
            for c in (100, 200, 300)
        ]
        assert len(create_transactions(user, items)) == 3


class TestView:
    def test_get_renders_rows_and_template(self, logged: Client, account: Account) -> None:
        html = logged.get(reverse(URL)).content.decode()
        assert html.count("data-batch-row ") >= 4  # 3 linhas + modelo para o JS
        assert 'id="batch-row-template"' in html
        assert "linhas-__prefix__-amount" in html

    def test_saves_rows_using_defaults_and_overrides(
        self, logged: Client, user: Any, account: Account
    ) -> None:
        card = create_account(user, name="Cartão", kind=AccountKind.CREDIT_CARD)
        rows = [
            {"amount": "45,90", "category": cat(user, "Alimentação").pk, "tags": "Restaurante"},
            {
                "amount": "12",
                "category": cat(user, "Transporte").pk,
                "account": card.pk,
                "date": "2026-10-10",
                "description": "Uber",
            },
            {"kind": "income", "amount": "800", "category": cat(user, "Outras receitas").pk},
            {},  # linha vazia do fim: ignorada
        ]
        response = logged.post(reverse(URL), batch_post(rows, account=account.pk))
        assert response.status_code == 302
        assert (
            response["Location"]
            == reverse("ledger:transaction_list") + "?de=2026-10-10&ate=2026-10-15"
        )
        txs = Transaction.objects.for_user(user)
        assert txs.count() == 3
        uber = txs.get(description="Uber")
        assert (uber.account, uber.date) == (card, datetime.date(2026, 10, 10))
        lunch = txs.get(amount_cents=4590)
        assert (lunch.account, lunch.date) == (account, TODAY)
        assert [t.name for t in lunch.tags.all()] == ["Restaurante"]
        message = logged.get(response["Location"]).content.decode()
        assert "3 movimentações registrada(s)" in message

    def test_row_errors_keep_values_and_save_nothing(
        self, logged: Client, user: Any, account: Account
    ) -> None:
        rows: list[dict[str, Any]] = [
            {"amount": "10", "category": cat(user, "Lazer").pk, "description": "Cinema"},
            {"amount": "abc", "category": cat(user, "Lazer").pk},
            {"amount": "5", "description": "sem categoria"},
            {"amount": "7", "category": cat(user, "Salário").pk},  # receita marcada como despesa
        ]
        response = logged.post(reverse(URL), batch_post(rows, account=account.pk))
        html = response.content.decode()
        assert response.status_code == 200
        assert "Nada foi salvo ainda" in html
        assert "Valor inválido" in html
        assert "Escolha uma categoria." in html
        assert 'value="Cinema"' in html  # o que foi digitado continua na tela
        assert not Transaction.objects.exists()

    def test_kind_mismatch_reported_by_service(
        self, logged: Client, user: Any, account: Account
    ) -> None:
        rows = [{"amount": "7", "category": cat(user, "Salário").pk}]
        html = logged.post(reverse(URL), batch_post(rows, account=account.pk)).content.decode()
        assert "é de receita" in html
        assert not Transaction.objects.exists()

    def test_requires_at_least_one_row(self, logged: Client, account: Account) -> None:
        html = logged.post(reverse(URL), batch_post([{}, {}], account=account.pk)).content.decode()
        assert "Preencha ao menos uma linha." in html

    def test_rejects_too_many_rows(self, logged: Client, user: Any, account: Account) -> None:
        rows = [{"amount": "1", "category": cat(user, "Lazer").pk}] * 101
        response = logged.post(reverse(URL), batch_post(rows, account=account.pk))
        assert response.status_code == 200
        assert not Transaction.objects.exists()

    def test_other_users_ids_are_rejected(
        self, logged: Client, user: Any, other_user: Any, account: Account
    ) -> None:
        theirs = ensure_default_account(other_user)
        rows = [{"amount": "1", "category": cat(other_user, "Lazer").pk}]
        response = logged.post(reverse(URL), batch_post(rows, account=theirs.pk))
        assert response.status_code == 200
        assert not Transaction.objects.exists()
