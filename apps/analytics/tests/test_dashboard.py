"""Os números da dashboard batem com o cálculo manual num cenário conhecido."""

import datetime
from decimal import Decimal
from typing import Any

import pytest
from django.utils import timezone

from apps.analytics import selectors
from apps.analytics.dashboard import build_dashboard, period_bounds
from apps.ledger.models import AccountKind
from apps.ledger.services import (
    TransactionData,
    create_account,
    create_transaction,
    ensure_default_account,
)
from apps.taxonomy.models import Category

pytestmark = pytest.mark.django_db
TODAY = datetime.date(2026, 10, 10)
D = datetime.date


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(timezone, "localdate", lambda *a, **k: TODAY)


@pytest.fixture
def scenario(user: Any) -> dict[str, Any]:
    checking = ensure_default_account(user)
    savings = create_account(user, name="Caixinha", kind=AccountKind.SAVINGS)
    cats = {c.name: c for c in Category.objects.for_user(user)}

    def add(
        kind: str, cents: int, day: datetime.date, category: str | None = None, **kw: Any
    ) -> None:
        create_transaction(
            user,
            TransactionData(
                kind=kind,
                amount_cents=cents,
                date=day,
                account=kw.pop("account", checking),
                category=cats[category] if category else None,
                **kw,
            ),
        )

    # Setembro: dias 1-10 e depois
    add("income", 500_000, D(2026, 9, 5), "Salário")
    add("expense", 10_000, D(2026, 9, 8), "Alimentação", tags=["Mercado"])
    add("expense", 90_000, D(2026, 9, 25), "Moradia")  # fora do recorte pro-rata (1-10)
    # Outubro até o dia 10
    add("income", 500_000, D(2026, 10, 5), "Salário")
    add(
        "expense",
        12_000,
        D(2026, 10, 2),
        "Alimentação",
        tags=["Mercado", "Delivery"],
        payment_method="credit",
    )
    add("expense", 3_000, D(2026, 10, 9), "Transporte", tags=["Uber"], payment_method="pix")
    add("transfer", 100_000, D(2026, 10, 6), destination_account=savings)  # nunca conta
    return cats


def test_period_bounds() -> None:
    assert period_bounds("mes", TODAY) == (D(2026, 10, 1), D(2026, 10, 31))
    assert period_bounds("mes-anterior", TODAY) == (D(2026, 9, 1), D(2026, 9, 30))
    assert period_bounds("3m", TODAY) == (D(2026, 8, 1), D(2026, 10, 31))
    assert period_bounds("12m", TODAY) == (D(2025, 11, 1), D(2026, 10, 31))
    assert period_bounds("ano", TODAY) == (D(2026, 1, 1), D(2026, 12, 31))


def test_kpis_compare_prorated_and_ignore_transfers(user: Any, scenario: Any) -> None:
    dash = build_dashboard(user, "mes", TODAY)
    kpis = {k.key: k for k in dash.kpis}
    assert kpis["income"].cents == 500_000
    assert kpis["expense"].cents == 15_000  # a transferência de 1.000,00 não entra
    assert kpis["net"].cents == 485_000
    # Comparação justa: 1-10/out contra 1-10/set (o aluguel de 25/set fica fora).
    expense_delta = kpis["expense"].delta
    assert expense_delta is not None
    assert expense_delta.previous_cents == 10_000
    assert expense_delta.pct == Decimal("50.0")
    assert expense_delta.is_good is False
    assert dash.comparison is not None
    assert dash.comparison.partial
    assert dash.daily_average_cents == 1_500  # 15.000 / 10 dias decorridos
    assert dash.savings_rate == Decimal("97.0")


def test_monthly_average_uses_only_closed_months(user: Any, scenario: Any) -> None:
    dash = build_dashboard(user, "mes", TODAY)
    assert dash.closed_months == 1  # só setembro tem dados entre os 3 meses fechados
    assert dash.monthly_average_cents == 100_000


def test_breakdowns_and_drilldown_links(user: Any, scenario: Any) -> None:
    dash = build_dashboard(user, "mes", TODAY)
    assert [(c["label"], c["cents"]) for c in dash.categories] == [
        ("Alimentação", 12_000),
        ("Transporte", 3_000),
    ]
    assert dash.categories[0]["pct"] == Decimal("80.0")
    assert dash.categories[0]["width"] == 100.0
    food = scenario["Alimentação"]
    assert f"categoria={food.pk}" in dash.categories[0]["link"]
    assert "de=2026-10-01" in dash.categories[0]["link"]
    assert "ate=2026-10-10" in dash.categories[0]["link"]
    tags = {t["label"]: t["cents"] for t in dash.tags}
    assert tags == {"Mercado": 12_000, "Delivery": 12_000, "Uber": 3_000}
    methods = {m["label"]: m["cents"] for m in dash.payment_methods}
    assert methods == {"Crédito": 12_000, "Pix": 3_000}


def test_monthly_series_include_empty_months(user: Any, scenario: Any) -> None:
    dash = build_dashboard(user, "mes", TODAY)
    chart = dash.monthly_chart
    assert chart["labels"] == ["mai/26", "jun/26", "jul/26", "ago/26", "set/26", "out/26"]
    income = chart["series"][0]["values"]
    expense = chart["series"][1]["values"]
    assert income == [0, 0, 0, 0, 500_000, 500_000]
    assert expense == [0, 0, 0, 0, 100_000, 15_000]
    assert dash.net_chart["values"][-2:] == [400_000, 485_000]


def test_pace_chart_is_cumulative(user: Any, scenario: Any) -> None:
    dash = build_dashboard(user, "mes", TODAY)
    assert dash.pace_chart is not None
    previous, current = dash.pace_chart["series"]
    assert len(current["values"]) == 10
    assert current["values"][0] == 0
    assert current["values"][1] == 12_000
    assert current["values"][-1] == 15_000
    assert len(previous["values"]) == 30
    assert previous["values"][-1] == 100_000
    assert build_dashboard(user, "3m", TODAY).pace_chart is None


def test_empty_user_has_no_data(other_user: Any) -> None:
    dash = build_dashboard(other_user, "mes", TODAY)
    assert not dash.has_data
    assert dash.categories == []
    assert all(k.delta is not None and k.delta.pct is None for k in dash.kpis)


def test_invalid_period_falls_back(user: Any) -> None:
    assert build_dashboard(user, "xyz", TODAY).period == "mes"


def test_scoped_to_user(user: Any, other_user: Any, scenario: Any) -> None:
    assert selectors.totals(other_user, None, None).expense_cents == 0
