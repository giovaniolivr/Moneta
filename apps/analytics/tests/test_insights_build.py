"""Montagem do contexto das análises a partir do banco (pro-rata, histórico completo, escopo)."""

import datetime
from typing import Any

import pytest

from apps.analytics import selectors
from apps.analytics.dashboard import build_dashboard
from apps.ledger.services import TransactionData, create_transaction, ensure_default_account
from apps.taxonomy.models import Category

pytestmark = pytest.mark.django_db
TODAY = datetime.date(2026, 10, 10)
D = datetime.date


@pytest.fixture
def add(user: Any) -> Any:
    account = ensure_default_account(user)
    cats = {c.name: c for c in Category.objects.for_user(user)}

    def _add(kind: str, cents: int, day: datetime.date, category: str) -> None:
        create_transaction(
            user,
            TransactionData(
                kind=kind, amount_cents=cents, date=day, account=account, category=cats[category]
            ),
        )

    return _add


@pytest.fixture
def history(add: Any) -> None:
    """Julho a setembro completos: mercado no dia 3 (R$ 100) e no dia 25 (R$ 500)."""
    add("income", 500_000, D(2026, 6, 1), "Salário")  # uso desde 1º/jun → jun é mês completo
    for month in (6, 7, 8, 9):
        add("expense", 10_000, D(2026, month, 3), "Alimentação")
        add("expense", 50_000, D(2026, month, 25), "Alimentação")


def test_partial_month_compares_with_same_days(user: Any, add: Any, history: None) -> None:
    add("expense", 20_000, D(2026, 10, 2), "Alimentação")  # dobro do usual para os dias 1-10
    dash = build_dashboard(user, "mes", TODAY)
    [alert] = [i for i in dash.findings if i.rule_id == "acima_da_media"]
    values = {e.label: e.value for e in alert.evidence}
    # Base = dias 1-10 de jul/ago/set (R$ 100), não os meses inteiros (R$ 600).
    assert values["Média dos mesmos dias (1 a 10) nos 3 meses anteriores"] == 10_000
    assert values["Variação"] == 100
    assert alert.filter is not None
    assert alert.filter.date_from == D(2026, 10, 1)
    assert alert.filter.date_to == TODAY


def test_closed_month_uses_whole_previous_months(user: Any, add: Any, history: None) -> None:
    add("expense", 90_000, D(2026, 9, 28), "Alimentação")  # setembro: 600 + 900
    dash = build_dashboard(user, "mes-anterior", TODAY)
    [alert] = [i for i in dash.findings if i.rule_id == "acima_da_media"]
    values = {e.label: e.value for e in alert.evidence}
    assert values["Média dos 3 meses anteriores"] == 60_000  # jun, jul, ago inteiros
    assert values["Alimentação em setembro"] == 150_000


def test_partial_first_month_is_not_history(user: Any, add: Any) -> None:
    add("expense", 10_000, D(2026, 7, 15), "Alimentação")  # julho começou pela metade
    add("expense", 10_000, D(2026, 10, 2), "Alimentação")
    dash = build_dashboard(user, "mes", TODAY)
    pending = {i.rule_id: i.message for i in dash.pending_insights}
    assert "(há 2)" in pending["acima_da_media"]  # só agosto e setembro
    assert "(há 2)" in pending["tendencia"]


def test_insights_are_scoped_to_user(user: Any, other_user: Any, history: None) -> None:
    assert build_dashboard(other_user, "mes", TODAY).insights == []
    assert selectors.first_activity_date(other_user) is None
    assert selectors.monthly_category_expenses(other_user, D(2026, 6, 1), TODAY) == {}
    assert selectors.category_names(other_user).keys().isdisjoint(selectors.category_names(user))
    assert selectors.first_activity_date(user) == D(2026, 6, 1)
