"""Rendimento estimado nas metas e na projeção de saldo."""

import datetime
from decimal import Decimal
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.goals.selectors import goal_rows
from apps.goals.services import create_goal
from apps.investments.models import Series
from apps.investments.services import ProductData, create_product, set_rate_override
from apps.ledger.models import AccountKind
from apps.ledger.services import (
    TransactionData,
    create_account,
    create_transaction,
    ensure_default_account,
    update_account,
)
from apps.planning.selectors import projection_data
from apps.taxonomy.models import Category

pytestmark = pytest.mark.django_db
D = datetime.date
TODAY = D(2026, 10, 14)


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(timezone, "localdate", lambda *a, **k: TODAY)


@pytest.fixture
def world(user: Any) -> dict[str, Any]:
    checking = ensure_default_account(user)
    update_account(checking, opening_balance_cents=500_000, opening_date=D(2026, 6, 1))
    product = create_product(user, ProductData("Caixinha", "cdi", Decimal(100), "taxed", True))
    savings = create_account(
        user,
        name="Reserva",
        kind=AccountKind.SAVINGS,
        opening_balance_cents=1_000_000,
        opening_date=D(2026, 6, 1),
        product=product,
    )
    yields = Category.objects.for_user(user).get(name="Rendimentos")
    for month in (7, 8, 9):
        create_transaction(
            user,
            TransactionData(
                kind="transfer",
                amount_cents=100_000,
                date=D(2026, month, 5),
                account=checking,
                destination_account=savings,
            ),
        )
        create_transaction(  # rendimento lançado pelo usuário: não é aporte
            user,
            TransactionData(
                kind="income",
                amount_cents=11_000,
                date=D(2026, month, 28),
                account=savings,
                category=yields,
            ),
        )
    return {"checking": checking, "savings": savings}


def test_goal_uses_transfers_as_contributions_and_adds_estimated_yield(
    user: Any, world: Any
) -> None:
    create_goal(
        user,
        name="Reserva",
        target_cents=3_000_000,
        account=world["savings"],
        deadline=D(2027, 9, 30),
    )
    [row] = goal_rows(user, TODAY)
    assert row.progress.pace_cents == 100_000  # só as transferências (sem os R$ 110)
    assert row.progress.monthly_rate == 0
    assert "sem a taxa CDI" in row.rate_note
    without_yield = row.progress.needed_monthly_cents

    set_rate_override(user, Series.CDI, Decimal("13.65"))
    [row] = goal_rows(user, TODAY)
    assert row.progress.monthly_rate > 0
    assert "Caixinha (100% do CDI)" in row.rate_note
    assert "líquido de IR de 22,5%" in row.rate_note  # idade média: 351 dias / 2 = 175
    with_yield = row.progress.needed_monthly_cents
    assert with_yield is not None
    assert without_yield is not None
    assert with_yield < without_yield


def test_projection_yields_on_business_days(user: Any, world: Any) -> None:
    set_rate_override(user, Series.CDI, Decimal("13.65"))
    savings = world["savings"].pk
    data = projection_data(user, TODAY, include_variable=False)
    proj = data.projection
    assert data.yielding == [world["savings"]]
    # Sábado e domingo (17-18/10) não rendem; segunda (19/10) rende.
    assert proj.balance_on(savings, D(2026, 10, 18)) == proj.balance_on(savings, D(2026, 10, 16))
    assert proj.balance_on(savings, D(2026, 10, 19)) > proj.balance_on(savings, D(2026, 10, 18))
    flat = projection_data(user, TODAY, include_variable=False, include_yield=False).projection
    assert flat.balance_on(savings, flat.days[-1]) == flat.balance_on(savings, TODAY)
    # Tudo o que a conta ganhou a mais na projeção é o rendimento estimado declarado.
    gained = proj.balance_on(savings, proj.days[-1]) - flat.balance_on(savings, flat.days[-1])
    assert gained == proj.yields[savings] > 0
    assert proj.balance_on(world["checking"].pk, proj.days[-1]) == flat.balance_on(
        world["checking"].pk, flat.days[-1]
    )  # sem produto: não rende


def test_projection_page_evidence(client: Client, user: Any, world: Any) -> None:
    client.force_login(user)
    missing = client.get(reverse("planning:projection")).content.decode()
    assert "Sem a taxa CDI o rendimento das contas não entra" in missing
    set_rate_override(user, Series.CDI, Decimal("13.65"))
    html = client.get(reverse("planning:projection")).content.decode()
    assert "Rendimento estimado até" in html
    assert "Reserva: 100% do CDI" in html
    assert "Líquido de IR estimado em 22,5%" in html
    off = client.get(reverse("planning:projection") + "?rendimento=0").content.decode()
    assert "Incluir rendimento estimado" in off
