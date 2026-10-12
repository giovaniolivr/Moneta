"""Gasto variável estimado, mês realizado vs previsto e o resumo de planejamento na dashboard."""

import datetime
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.ledger.services import TransactionData, create_transaction, ensure_default_account
from apps.planning.selectors import month_outlook, projection_data, variable_spending
from apps.planning.services import RecurrenceData, create_recurrence
from apps.taxonomy.models import Category

pytestmark = pytest.mark.django_db
D = datetime.date
TODAY = D(2026, 10, 21)  # faltam 10 dias no mês


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(timezone, "localdate", lambda *a, **k: TODAY)


@pytest.fixture
def world(user: Any) -> dict[str, Any]:
    account = ensure_default_account(user)
    account.opening_date = D(2026, 6, 1)
    account.opening_balance_cents = 100_000
    account.save()
    cats = {c.name: c for c in Category.objects.for_user(user)}

    def add(kind: str, cents: int, day: datetime.date, name: str, **kw: Any) -> None:
        create_transaction(
            user,
            TransactionData(
                kind=kind, amount_cents=cents, date=day, account=account, category=cats[name], **kw
            ),
        )

    add("expense", 1_000, D(2026, 6, 1), "Outros")  # uso desde 1º/jun
    for month in (7, 8, 9):
        add("expense", 60_000, D(2026, month, 10), "Alimentação")  # dia a dia: R$ 600/mês
    add("income", 500_000, D(2026, 10, 5), "Salário")
    add("expense", 40_000, D(2026, 10, 8), "Alimentação")
    rent = RecurrenceData(
        entry=TransactionData(
            kind="expense",
            amount_cents=150_000,
            date=TODAY,
            account=account,
            category=cats["Moradia"],
            description="Aluguel",
        ),
        frequency="monthly",
        interval=1,
        start_date=D(2026, 10, 25),
    )
    create_recurrence(user, rent, today=TODAY)
    return {"account": account}


def test_variable_spending_uses_complete_months_only(user: Any, world: Any) -> None:
    estimate = variable_spending(user, TODAY)
    # Últimos 3 meses completos (jul, ago, set); outubro está em andamento e fica de fora.
    assert estimate.months == 3
    assert estimate.monthly_by_account == {world["account"].pk: 60_000}
    assert estimate.daily_by_account() == {world["account"].pk: 1_973}  # 600 * 12 / 365


def test_month_outlook(user: Any, world: Any) -> None:
    outlook = month_outlook(user, TODAY)
    assert outlook.net_cents == 460_000
    assert outlook.planned_expense_cents == 150_000  # aluguel previsto do dia 25
    assert outlook.variable_cents == 19_726  # 10 dias de dia a dia estimado
    assert outlook.projected_net_cents == 460_000 - 150_000 - 19_726


def test_projection_with_and_without_variable(user: Any, world: Any) -> None:
    account = world["account"].pk
    with_variable = projection_data(user, TODAY).projection
    without = projection_data(user, TODAY, include_variable=False).projection
    end = D(2026, 10, 31)
    assert without.balance_on(account, end) - with_variable.balance_on(account, end) == 1_973 * 10


def test_projection_page_toggle(client: Client, user: Any, world: Any) -> None:
    client.force_login(user)
    html = client.get(reverse("planning:projection")).content.decode()
    assert "gasto do dia a dia estimado em R$\xa0600,00/mês" in html
    off = client.get(reverse("planning:projection") + "?variavel=0").content.decode()
    assert "Incluir gasto variável estimado" in off


def test_dashboard_planning_card(client: Client, user: Any, world: Any) -> None:
    client.force_login(user)
    html = client.get(reverse("home")).content.decode()
    assert 'id="planning-title"' in html
    assert "Resultado previsto do mês" in html
    assert "25/10 · Aluguel" in html  # próximos 7 dias
    other_period = client.get(reverse("home") + "?periodo=3m").content.decode()
    assert 'id="planning-title"' not in other_period


def test_dashboard_without_planning_has_no_card(client: Client, other_user: Any) -> None:
    account = ensure_default_account(other_user)
    food = Category.objects.for_user(other_user).get(name="Alimentação")
    create_transaction(
        other_user,
        TransactionData(
            kind="expense", amount_cents=1_000, date=TODAY, account=account, category=food
        ),
    )
    client.force_login(other_user)
    assert 'id="planning-title"' not in client.get(reverse("home")).content.decode()


def test_negative_alert_on_dashboard(client: Client, user: Any, world: Any) -> None:
    moradia = Category.objects.for_user(user).get(name="Moradia")
    create_transaction(
        user,
        TransactionData(
            kind="expense",
            amount_cents=900_000,
            date=D(2026, 10, 28),
            account=world["account"],
            category=moradia,
            status="planned",
        ),
    )
    client.force_login(user)
    html = client.get(reverse("home")).content.decode()
    assert "deve ficar negativa em 28/10" in html


def test_recurrence_history_entered_by_hand_is_not_counted_twice(user: Any, world: Any) -> None:
    """Aluguel lançado à mão em jul-set e só depois virou recorrência: sai do dia a dia."""
    account = world["account"]
    moradia = Category.objects.for_user(user).get(name="Moradia")
    for month in (7, 8, 9):
        create_transaction(
            user,
            TransactionData(
                kind="expense",
                amount_cents=150_000,
                date=D(2026, month, 25),
                account=account,
                category=moradia,
            ),
        )
    estimate = variable_spending(user, TODAY)
    assert estimate.monthly_by_account == {account.pk: 60_000}  # só o dia a dia de verdade
