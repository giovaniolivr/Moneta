"""Juntar e comprar depois, quanto guardar por mês e "cabe no bolso"."""

import datetime
from decimal import Decimal
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.investments.domain.portfolio import Portfolio
from apps.investments.domain.purchase import (
    Comparison,
    Payment,
    Strategy,
    StrategyResult,
    monthly_to_reach,
    months_to_save,
)
from apps.investments.domain.rates import CdiPercent, Prefixed
from apps.investments.domain.taxes import EXEMPT, TAXED
from apps.investments.models import Series
from apps.investments.selectors import Pocket, fits
from apps.investments.services import set_rate_override
from apps.ledger.services import TransactionData, create_transaction, ensure_default_account
from apps.taxonomy.models import Category

D = datetime.date
TODAY = D(2026, 10, 14)
NO_YIELD = Prefixed(Decimal(0))
CDI = CdiPercent(Decimal("0.1365"), Decimal(100))


class TestDomain:
    def test_months_to_save_without_yield(self) -> None:
        plan = months_to_save(
            yield_=NO_YIELD, taxes=EXEMPT, start=TODAY, monthly_cents=30_000, target_cents=285_000
        )
        assert plan is not None
        assert (plan.months, plan.purchase_day) == (10, D(2027, 8, 14))  # 10 x R$ 300
        assert plan.deposited_cents == 300_000
        assert (
            months_to_save(
                yield_=NO_YIELD, taxes=EXEMPT, start=TODAY, monthly_cents=0, target_cents=1
            )
            is None
        )

    def test_yield_helps_but_never_hurts(self) -> None:
        plan = months_to_save(
            yield_=CDI, taxes=TAXED, start=TODAY, monthly_cents=30_000, target_cents=300_000
        )
        assert plan is not None
        assert plan.months <= 10
        assert plan.net_at_purchase_cents >= 300_000

    def test_monthly_to_reach_is_minimal(self) -> None:
        deadline = D(2027, 10, 14)
        assert (
            monthly_to_reach(
                yield_=NO_YIELD, taxes=EXEMPT, start=TODAY, target_cents=120_000, deadline=deadline
            )
            == 10_000
        )  # 12 aportes de R$ 100
        needed = monthly_to_reach(
            yield_=CDI, taxes=TAXED, start=TODAY, target_cents=120_000, deadline=deadline
        )
        assert needed < 10_000

        def net(monthly: int) -> int:
            p = Portfolio(CDI, TAXED)
            for m in range(12):
                p.deposit(D(2026 + (10 + m - 1) // 12, (10 + m - 1) % 12 + 1, 14), monthly)
            return p.value(deadline).net_cents

        assert net(needed) >= 120_000 > net(needed - 1)


def _comparison(*strategies: Strategy) -> Comparison:
    return Comparison(TODAY, 0, [StrategyResult(s, 0, s.total_cents, 0, None) for s in strategies])


def test_fits_verdicts() -> None:
    cash = Strategy("avista", "À vista", (Payment(TODAY, 200_000),))
    tenx = Strategy("sem-juros", "10x", tuple(Payment(D(2026, m, 1), 20_000) for m in (11, 12)))
    money = Pocket(capacity_cents=15_000, capacity_months=3, balance_cents=500_000)
    cash_fit, tenx_fit = fits(_comparison(cash, tenx), money)
    assert (cash_fit.verdict, cash_fit.detail) == ("cabe", "usa 40% do saldo em contas hoje")
    assert tenx_fit.verdict == "aperta"
    assert "acima dos R$\xa0150,00 que você costuma guardar" in tenx_fit.detail
    broke = fits(_comparison(cash), Pocket(None, 0, 100_000))[0]
    assert broke.verdict == "aperta"
    no_history = fits(_comparison(tenx), Pocket(None, 0, 0))[0]
    assert no_history.verdict == "sem base"


@pytest.mark.django_db
class TestPage:
    @pytest.fixture(autouse=True)
    def _fixed_today(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(timezone, "localdate", lambda *a, **k: TODAY)

    def test_saving_section_and_goal_link(self, client: Client, user: Any) -> None:
        account = ensure_default_account(user)
        account.opening_date = D(2026, 6, 1)
        account.opening_balance_cents = 400_000
        account.save()
        salary = Category.objects.for_user(user).get(name="Salário")
        for month in (7, 8, 9):
            create_transaction(
                user,
                TransactionData(
                    kind="income",
                    amount_cents=50_000,
                    date=D(2026, month, 1),
                    account=account,
                    category=salary,
                ),
            )
        client.force_login(user)
        html = client.get(
            reverse("investments:purchase"),
            {
                "valor": "3.000,00",
                "produto": "nenhum",
                "desconto": "5",
                "sj_parcelas": "10",
                "guardar": "300,00",
                "ate": "2027-10-14",
            },
        ).content.decode()
        assert "Juntar antes de comprar" in html
        assert "você compra à vista por R$\xa02.850,00 em <strong>08/2027</strong>" in html
        assert "vs. comprar agora em 10x sem juros" in html
        assert "economiza R$\xa00,00" not in html
        assert "guarde <strong>R$\xa0237,50/mês</strong>" in html  # 2.850 / 12, sem rendimento
        assert "Cabe no bolso?" in html
        assert "R$\xa0300,00/mês de R$\xa0500,00 que você costuma guardar" in html
        assert "prazo=2027-10-14" in html
        goal_form = client.get(
            reverse("goals:goal_create") + "?nome=Compra+planejada&alvo=2850.00&prazo=2027-10-14",
            headers={"X-Moneta-Fragment": "1"},
        ).content.decode()
        assert 'value="Compra planejada"' in goal_form
        assert 'value="2850,00"' in goal_form
        assert 'value="2027-10-14"' in goal_form

    def test_deadline_must_be_future(self, client: Client, user: Any) -> None:
        client.force_login(user)
        html = client.get(
            reverse("investments:purchase"),
            {"valor": "1.000,00", "produto": "nenhum", "ate": "2026-01-01"},
        ).content.decode()
        assert "Escolha uma data futura" in html

    def test_with_yield(self, client: Client, user: Any) -> None:
        set_rate_override(user, Series.CDI, Decimal("13.65"))
        client.force_login(user)
        html = client.get(
            reverse("investments:purchase"),
            {"valor": "3.000,00", "produto": "ref-cdi", "ate": "2027-10-14"},
        ).content.decode()
        assert "já contando rendimento, IR e IOF" in html
        assert "guarde <strong>R$\xa0250,00/mês</strong>" not in html  # rende: menos que 3.000/12
