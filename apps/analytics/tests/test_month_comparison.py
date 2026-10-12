"""Comparativo mês a mês por categoria e variações das listas de tags/formas de pagamento."""

import datetime
from decimal import Decimal
from typing import Any

import pytest

from apps.analytics.dashboard import build_dashboard, build_month_comparison
from apps.dashboard.templatetags.dashboard_ui import spark_points
from apps.ledger.services import TransactionData, create_transaction, ensure_default_account
from apps.taxonomy.models import Category

pytestmark = pytest.mark.django_db
TODAY = datetime.date(2026, 10, 10)
D = datetime.date


@pytest.fixture
def scenario(user: Any) -> Any:
    account = ensure_default_account(user)
    cats = {c.name: c for c in Category.objects.for_user(user)}

    def add(cents: int, day: datetime.date, category: str = "Alimentação", **kw: Any) -> None:
        create_transaction(
            user,
            TransactionData(
                kind="expense",
                amount_cents=cents,
                date=day,
                account=account,
                category=cats[category],
                **kw,
            ),
        )

    add(10_000, D(2026, 7, 15))  # uso começou no meio de julho → julho não é base
    add(10_000, D(2026, 8, 3))
    add(50_000, D(2026, 8, 25))
    add(20_000, D(2026, 9, 3), tags=["Mercado"], payment_method="pix")
    add(50_000, D(2026, 9, 25))
    add(30_000, D(2026, 10, 2), tags=["Mercado"], payment_method="pix")
    add(5_000, D(2026, 10, 4), "Transporte")
    return cats


def test_partial_month_against_same_days_average(user: Any, scenario: Any) -> None:
    cmp = build_month_comparison(user, TODAY, D(2026, 7, 15))
    assert cmp is not None
    assert cmp.months == ["mai/26", "jun/26", "jul/26", "ago/26", "set/26", "out/26"]
    assert cmp.partial_day == 10
    assert cmp.baseline_months == 2  # agosto e setembro (julho começou pela metade)
    food, transport = cmp.rows
    assert food.label == "Alimentação"
    assert [c.cents for c in food.cells] == [0, 0, 10_000, 60_000, 70_000, 30_000]
    # Base pro-rata: dias 1-10 de agosto (100) e setembro (200), não os meses inteiros.
    assert food.average_cents == 15_000
    assert food.delta is not None
    assert food.delta.pct == Decimal("100.0")
    assert food.delta.is_good is False
    assert food.spark == [60_000, 70_000]  # só meses fechados completos
    assert "ate=2026-10-10" in food.cells[-1].link
    assert f"categoria={scenario['Alimentação'].pk}" in food.cells[-1].link
    assert transport.average_cents == 0
    assert transport.delta is not None
    assert transport.delta.pct is None  # sem base: a tela mostra "novo", não um percentual
    assert [c.cents for c in cmp.total.cells][-1] == 35_000
    assert cmp.total.average_cents == 15_000


def test_closed_month_against_whole_months(user: Any, scenario: Any) -> None:
    cmp = build_month_comparison(user, D(2026, 9, 30), D(2026, 7, 15))
    assert cmp is not None
    assert cmp.partial_day is None
    [food] = cmp.rows
    assert food.average_cents == 60_000  # agosto inteiro
    assert food.delta is not None
    assert food.delta.pct == Decimal("16.7")
    assert food.spark == [60_000, 70_000]


def test_scoped_to_user(user: Any, other_user: Any, scenario: Any) -> None:
    assert build_month_comparison(other_user, TODAY, D(2026, 7, 15)) is None
    assert build_dashboard(other_user, "mes", TODAY).month_comparison is None


def test_tag_and_payment_rows_compare_with_previous_period(user: Any, scenario: Any) -> None:
    dash = build_dashboard(user, "mes", TODAY)
    [tag] = dash.tags
    assert tag["delta"].previous_cents == 20_000  # 1-10/set
    assert tag["delta"].pct == Decimal("50.0")
    assert "tipo=expense" in tag["link"]  # o valor é de despesas; o drill-down também
    methods = {m["label"]: m["delta"] for m in dash.payment_methods}
    assert methods["Pix"].pct == Decimal("50.0")
    assert methods["Não informado"].previous_cents == 0


def test_spark_points_start_at_zero() -> None:
    assert spark_points([5]) == ""
    # Valores estáveis e altos ficam perto do topo, não esticados de ponta a ponta.
    assert spark_points([100, 98, 100]) == "0.0,1.0 50.0,1.4 100.0,1.0"
    assert spark_points([0, 0]) == "0.0,23.0 100.0,23.0"
