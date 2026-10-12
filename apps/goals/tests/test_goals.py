"""Metas: progresso pelo saldo da conta, aporte necessário, ritmo real e capacidade."""

import datetime
from decimal import Decimal
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.goals.domain.progress import goal_progress, months_until, needed_payment
from apps.goals.models import Goal
from apps.goals.selectors import goal_rows
from apps.goals.services import create_goal, update_goal
from apps.ledger.models import AccountKind
from apps.ledger.services import (
    TransactionData,
    create_account,
    create_transaction,
    ensure_default_account,
)
from apps.taxonomy.models import Category

D = datetime.date
TODAY = D(2026, 10, 12)
FRAGMENT = {"X-Moneta-Fragment": "1"}


def progress(**kw: Any) -> Any:
    base: dict[str, Any] = {
        "target_cents": 2_000_000,
        "monthly_contributions": [100_000, 100_000, 100_000],
        "current_balance_cents": 800_000,
        "monthly_results": [150_000, 120_000, 150_000],
        "today": TODAY,
        "deadline": D(2027, 9, 30),
    }
    return goal_progress(**(base | kw))


class TestDomain:
    def test_months(self) -> None:
        assert months_until(TODAY, D(2026, 10, 31)) == 1
        assert months_until(TODAY, D(2027, 9, 30)) == 12
        assert months_until(TODAY, D(2025, 1, 1)) == 1  # prazo vencido: o que falta, já

    def test_on_track(self) -> None:
        p = progress()
        assert (p.remaining_cents, p.pct) == (1_200_000, 40)
        assert p.needed_monthly_cents == 100_000  # R$ 12 mil em 12 meses
        assert (p.pace_cents, p.capacity_cents) == (100_000, 140_000)
        assert p.status == "on_track"

    def test_behind_and_beyond_capacity(self) -> None:
        assert progress(monthly_contributions=[50_000, 50_000]).status == "behind"
        tight = progress(deadline=D(2027, 1, 31))  # R$ 12 mil em 4 meses = R$ 3 mil/mês
        assert tight.needed_monthly_cents == 300_000
        assert tight.status == "beyond_capacity"

    def test_no_deadline_forecast(self) -> None:
        p = progress(deadline=None)
        assert p.status == "no_deadline"
        assert p.forecast_month == D(2027, 9, 1)  # 12 aportes de R$ 1.000 a partir de outubro
        assert progress(deadline=None, monthly_contributions=[0, 0]).forecast_month is None

    def test_no_pace_without_history(self) -> None:
        p = progress(monthly_contributions=[], monthly_results=[])
        assert (p.pace_cents, p.history_months, p.status) == (None, 0, "no_pace")

    def test_yield_lowers_needed_payment_and_brings_forecast_forward(self) -> None:
        rate = Decimal("0.009")  # ~0,9% ao mês líquido
        with_yield = progress(monthly_rate=rate)
        # 1,009^12 ≈ 1,11351: 800k rende até ≈ 890.808; faltam ≈ 1.109.192, divididos pelo
        # fator (1,11351 - 1)/0,009 ≈ 12,612 → P ≈ R$ 879,47 (sem rendimento: R$ 1.000).
        assert with_yield.needed_monthly_cents == 87_947
        assert with_yield.needed_monthly_cents < progress().needed_monthly_cents
        no_deadline = progress(deadline=None, monthly_rate=rate)
        assert no_deadline.forecast_month is not None
        assert no_deadline.forecast_month < progress(deadline=None).forecast_month
        # Só rendimento, sem aportes: ainda chega lá (devagar).
        assert progress(deadline=None, monthly_contributions=[0], monthly_rate=rate).forecast_month

    def test_needed_payment_without_rate(self) -> None:
        assert needed_payment(1_200_000, 800_000, 12, Decimal(0)) == 100_000

    def test_achieved(self) -> None:
        p = progress(current_balance_cents=2_100_000)
        assert (p.status, p.pct, p.remaining_cents) == ("achieved", 100, 0)


@pytest.mark.django_db
class TestWithData:
    @pytest.fixture(autouse=True)
    def _fixed_today(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(timezone, "localdate", lambda *a, **k: TODAY)

    @pytest.fixture
    def world(self, user: Any) -> dict[str, Any]:
        checking = ensure_default_account(user)
        checking.opening_date = D(2026, 6, 1)
        checking.save()
        savings = create_account(
            user,
            name="Caixinha",
            kind=AccountKind.SAVINGS,
            opening_balance_cents=500_000,
            opening_date=D(2026, 6, 1),
        )
        salary = Category.objects.for_user(user).get(name="Salário")
        food = Category.objects.for_user(user).get(name="Alimentação")
        for month in (6, 7, 8, 9):
            day = D(2026, month, 5)
            entries: tuple[dict[str, Any], ...] = (
                {"kind": "income", "amount_cents": 500_000, "category": salary},
                {"kind": "expense", "amount_cents": 350_000, "category": food},
                {"kind": "transfer", "amount_cents": 100_000, "destination_account": savings},
            )
            for kw in entries:
                create_transaction(user, TransactionData(date=day, account=checking, **kw))
        return {"checking": checking, "savings": savings}

    def test_rows_use_account_balance_and_history(self, user: Any, world: Any) -> None:
        create_goal(
            user,
            name="Reserva",
            target_cents=2_000_000,
            account=world["savings"],
            deadline=D(2027, 9, 30),
        )
        [row] = goal_rows(user, TODAY)
        p = row.progress
        assert p.balance_cents == 900_000  # R$ 5 mil iniciais + 4 aportes de R$ 1 mil
        assert p.pace_cents == 100_000  # jul, ago, set
        assert p.capacity_cents == 150_000
        assert p.needed_monthly_cents == 91_667  # R$ 11 mil / 12 meses, arredondado para cima
        assert p.status == "on_track"

    def test_rules(self, user: Any, other_user: Any, world: Any) -> None:
        card = create_account(user, name="Cartão", kind=AccountKind.CREDIT_CARD)
        with pytest.raises(ValidationError, match="dinheiro fica guardado"):
            create_goal(user, name="X", target_cents=1, account=card)
        with pytest.raises(ValidationError, match="inválida"):
            create_goal(user, name="X", target_cents=1, account=ensure_default_account(other_user))
        goal = create_goal(user, name="Reserva", target_cents=100, account=world["savings"])
        with pytest.raises(ValidationError, match="já tem uma meta"):
            create_goal(user, name="Outra", target_cents=100, account=world["savings"])
        update_goal(
            goal, name="Reserva 2", target_cents=200, account=world["savings"], deadline=None
        )
        assert Goal.objects.get().target_cents == 200

    def test_views(self, client: Client, user: Any, world: Any) -> None:
        client.force_login(user)
        response = client.post(
            reverse("goals:goal_create"),
            {
                "name": "Reserva de emergência",
                "target": "20.000,00",
                "account": str(world["savings"].pk),
                "deadline": "2027-09-30",
            },
            headers=FRAGMENT,
        )
        assert response.status_code == 204
        page = client.get(reverse("planning:index")).content.decode()
        assert "Reserva de emergência" in page
        assert "No ritmo: precisa de R$\xa0916,67/mês até 09/2027" in page
        form = client.get(reverse("goals:goal_create"), headers=FRAGMENT).content.decode()
        assert f'value="{world["savings"].pk}"' not in form  # conta já tem meta
        goal = Goal.objects.get()
        client.post(reverse("goals:goal_delete", args=[goal.pk]), headers=FRAGMENT)
        assert not Goal.objects.exists()

    @pytest.mark.parametrize("name", ["goals:goal_update", "goals:goal_delete"])
    def test_other_users_goals_are_404(
        self, client: Client, user: Any, other_user: Any, world: Any, name: str
    ) -> None:
        goal = create_goal(user, name="Reserva", target_cents=100, account=world["savings"])
        client.force_login(other_user)
        assert client.post(reverse(name, args=[goal.pk])).status_code == 404
        assert Goal.objects.filter(pk=goal.pk).exists()
        assert goal_rows(other_user, TODAY) == []
