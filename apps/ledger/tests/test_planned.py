"""Movimentações previstas: fora de saldos/análises, confirmação e tela de previstas."""

import datetime
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.analytics import selectors as analytics
from apps.analytics.dashboard import build_dashboard
from apps.ledger.filters import TransactionFilter
from apps.ledger.models import Transaction, TransactionStatus
from apps.ledger.selectors import account_balances, filter_transactions
from apps.ledger.services import (
    TransactionData,
    confirm_transaction,
    create_transaction,
    ensure_default_account,
)
from apps.taxonomy.models import Category

pytestmark = pytest.mark.django_db
TODAY = datetime.date(2026, 10, 10)
D = datetime.date
FRAGMENT = {"X-Moneta-Fragment": "1"}


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(timezone, "localdate", lambda *a, **k: TODAY)


@pytest.fixture
def world(user: Any) -> dict[str, Any]:
    account = ensure_default_account(user)
    account.opening_date = D(2026, 1, 1)
    account.save()
    cats = {c.name: c for c in Category.objects.for_user(user)}

    def add(cents: int, day: datetime.date, status: str = "posted", name: str = "Moradia") -> Any:
        return create_transaction(
            user,
            TransactionData(
                kind="expense",
                amount_cents=cents,
                date=day,
                account=account,
                category=cats[name],
                status=status,
            ),
        )

    return {
        "account": account,
        "posted": add(10_000, D(2026, 10, 2), name="Alimentação"),
        "overdue": add(150_000, D(2026, 10, 5), "planned"),  # aluguel ainda não confirmado
        "future": add(80_000, D(2026, 11, 5), "planned"),
    }


def test_planned_never_counts(user: Any, world: Any) -> None:
    assert analytics.totals(user, D(2026, 10, 1), D(2026, 11, 30)).expense_cents == 10_000
    assert account_balances(user, on=D(2026, 12, 31))[world["account"].pk] == -10_000
    in_list = filter_transactions(user, TransactionFilter())
    assert list(in_list) == [world["posted"]]
    planned = filter_transactions(user, TransactionFilter(status="planned"))
    assert set(planned) == {world["overdue"], world["future"]}
    dash = build_dashboard(user, "mes", TODAY)
    assert dash.totals.expense_cents == 10_000


def test_only_planned_may_be_in_the_future(user: Any, world: Any) -> None:
    tx = world["future"]
    tx.status = TransactionStatus.POSTED
    with pytest.raises(ValidationError, match="data futura"):
        tx.full_clean()


class TestConfirm:
    def test_overdue_keeps_its_date(self, world: Any) -> None:
        tx = confirm_transaction(world["overdue"], today=TODAY)
        assert (tx.status, tx.date) == (TransactionStatus.POSTED, D(2026, 10, 5))

    def test_early_payment_is_dated_today(self, world: Any) -> None:
        tx = confirm_transaction(world["future"], today=TODAY)
        assert (tx.status, tx.date) == (TransactionStatus.POSTED, TODAY)

    def test_posted_cannot_be_confirmed(self, world: Any) -> None:
        with pytest.raises(ValidationError, match="já está realizada"):
            confirm_transaction(world["posted"], today=TODAY)

    def test_view(self, client: Client, user: Any, world: Any) -> None:
        client.force_login(user)
        url = reverse("ledger:transaction_confirm", args=[world["overdue"].pk])
        response = client.post(url, {"next": "/"})
        assert response["Location"] == "/"
        world["overdue"].refresh_from_db()
        assert world["overdue"].status == TransactionStatus.POSTED
        # Já confirmada (ou realizada): não é mais uma prevista → 404.
        assert client.post(url).status_code == 404

    def test_other_users_planned_are_404(self, client: Client, other_user: Any, world: Any) -> None:
        client.force_login(other_user)
        url = reverse("ledger:transaction_confirm", args=[world["overdue"].pk])
        assert client.post(url).status_code == 404
        world["overdue"].refresh_from_db()
        assert world["overdue"].status == TransactionStatus.PLANNED


class TestEditForm:
    def _post(self, client: Client, tx: Transaction, **extra: str) -> Any:
        data = {
            "kind": "expense",
            "amount": "1.600,00",
            "category": str(tx.category_id),
            "account": str(tx.account_id),
            "date": tx.date.isoformat(),
            **extra,
        }
        return client.post(
            reverse("ledger:transaction_update", args=[tx.pk]), data, headers=FRAGMENT
        )

    def test_edit_keeps_planned_and_confirm_posts(
        self, client: Client, user: Any, world: Any
    ) -> None:
        client.force_login(user)
        tx = world["overdue"]
        assert self._post(client, tx).status_code == 204
        tx.refresh_from_db()
        assert (tx.status, tx.amount_cents) == (TransactionStatus.PLANNED, 160_000)
        assert self._post(client, tx, confirm="1").status_code == 204
        tx.refresh_from_db()
        assert tx.status == TransactionStatus.POSTED

    def test_confirm_with_future_date_is_refused(
        self, client: Client, user: Any, world: Any
    ) -> None:
        client.force_login(user)
        html = self._post(client, world["future"], confirm="1").content.decode()
        assert "Para confirmar, use a data de hoje" in html
        world["future"].refresh_from_db()
        assert world["future"].status == TransactionStatus.PLANNED

    def test_planned_form_offers_confirmation(self, client: Client, user: Any, world: Any) -> None:
        client.force_login(user)
        html = client.get(
            reverse("ledger:transaction_update", args=[world["future"].pk]), headers=FRAGMENT
        ).content.decode()
        assert "Salvar e confirmar" in html


class TestPages:
    def test_planned_list_sections(self, client: Client, user: Any, world: Any) -> None:
        client.force_login(user)
        html = client.get(reverse("ledger:planned_list")).content.decode()
        due, upcoming = html.split('id="upcoming-title"')
        assert reverse("ledger:transaction_confirm", args=[world["overdue"].pk]) in due
        assert reverse("ledger:transaction_confirm", args=[world["future"].pk]) in upcoming

    def test_list_links_to_planned_with_count(self, client: Client, user: Any, world: Any) -> None:
        client.force_login(user)
        html = client.get(reverse("ledger:transaction_list")).content.decode()
        assert reverse("ledger:planned_list") in html
        assert '<span class="badge">2</span>' in html

    def test_dashboard_warns_about_due_planned(self, client: Client, user: Any, world: Any) -> None:
        client.force_login(user)
        html = client.get(reverse("home")).content.decode()
        assert "1 movimentação prevista chegou à data" in html
        assert reverse("ledger:planned_list") in html

    def test_planned_list_scoped(self, client: Client, other_user: Any, world: Any) -> None:
        client.force_login(other_user)
        html = client.get(reverse("ledger:planned_list")).content.decode()
        assert "Nenhuma movimentação prevista" in html
        assert "Nada a confirmar" in html
