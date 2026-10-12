"""Simulador de investimento: aportes, impostos por aporte, inflação, tela e escopo."""

import datetime
import time
from decimal import Decimal
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.investments.domain.portfolio import Portfolio
from apps.investments.domain.rates import CdiPercent, Prefixed
from apps.investments.domain.simulation import simulate
from apps.investments.domain.taxes import EXEMPT, TAXED
from apps.investments.models import Series
from apps.investments.services import ProductData, create_product, set_rate_override

D = datetime.date
TODAY = D(2026, 10, 14)


class TestDomain:
    def test_initial_only_matches_portfolio(self) -> None:
        sim = simulate(
            yield_=Prefixed(Decimal("0.12")),
            taxes=TAXED,
            start=TODAY,
            initial_cents=1_000_000,
            monthly_cents=0,
            months=12,
        )
        manual = Portfolio(Prefixed(Decimal("0.12")), TAXED)
        manual.deposit(TODAY, 1_000_000)
        assert sim.final.valuation == manual.value(D(2027, 10, 14))
        assert sim.final.invested_cents == 1_000_000
        assert len(sim.points) == 13  # hoje + 12 meses

    def test_monthly_contributions(self) -> None:
        sim = simulate(
            yield_=Prefixed(Decimal("0.12")),
            taxes=EXEMPT,
            start=TODAY,
            initial_cents=0,
            monthly_cents=10_000,
            months=12,
        )
        assert sim.final.invested_cents == 120_000  # 12 aportes em 12 meses
        assert sim.final.valuation.net_cents > 120_000
        assert sim.points[1].invested_cents == 10_000  # antes do aporte do mês 1

    def test_long_horizon_uses_yearly_points_and_is_fast(self) -> None:
        began = time.perf_counter()
        sim = simulate(
            yield_=CdiPercent(Decimal("0.1365"), Decimal(100)),
            taxes=TAXED,
            start=TODAY,
            initial_cents=0,
            monthly_cents=50_000,
            months=360,
        )
        assert time.perf_counter() - began < 5
        assert len(sim.points) == 31  # hoje + 30 anos
        assert sim.final.invested_cents == 360 * 50_000

    def test_real_value(self) -> None:
        sim = simulate(
            yield_=Prefixed(Decimal("0.10")),
            taxes=EXEMPT,
            start=D(2026, 1, 1),
            initial_cents=100_000,
            monthly_cents=0,
            months=12,
        )
        real = sim.real_value(Decimal("0.05"))
        assert real is not None
        assert abs(real - sim.final.valuation.net_cents / 1.05) < 2
        assert sim.real_value(None) is None

    def test_invalid_horizon(self) -> None:
        with pytest.raises(ValueError, match="prazo"):
            simulate(
                yield_=Prefixed(Decimal("0.1")),
                taxes=TAXED,
                start=TODAY,
                initial_cents=1,
                monthly_cents=0,
                months=0,
            )


@pytest.mark.django_db
class TestPage:
    @pytest.fixture(autouse=True)
    def _fixed_today(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(timezone, "localdate", lambda *a, **k: TODAY)

    def test_compares_products_and_marks_best(self, client: Client, user: Any) -> None:
        set_rate_override(user, Series.CDI, Decimal("13.65"))
        set_rate_override(user, Series.SELIC, Decimal("13.75"))
        set_rate_override(user, Series.IPCA, Decimal("4.5"))
        turbo = create_product(user, ProductData("Turbo", "cdi", Decimal(120), "taxed", True))
        client.force_login(user)
        url = reverse("investments:simulator")
        html = client.get(
            url,
            {
                "inicial": "10.000,00",
                "aporte": "500,00",
                "meses": "24",
                "produto": [str(turbo.pk), "ref-cdi", "ref-poupanca"],
            },
        ).content.decode()
        assert "Resultado no resgate" in html
        best_row = html.split("maior líquido")[0].rsplit("<th", 1)[1]
        assert "Turbo" in best_row  # 120% do CDI vence 100% do CDI e a poupança
        assert html.count("maior líquido") == 1
        assert 'id="data-simulation"' in html
        assert "R$\xa022.000,00" in html  # investido: 10 mil + 24 * 500

    def test_missing_rate_is_declared(self, client: Client, user: Any) -> None:
        client.force_login(user)
        html = client.get(
            reverse("investments:simulator"), {"inicial": "1.000,00", "meses": "12"}
        ).content.decode()
        assert "Falta a taxa CDI" in html

    def test_requires_some_amount(self, client: Client, user: Any) -> None:
        client.force_login(user)
        html = client.get(reverse("investments:simulator"), {"meses": "12"}).content.decode()
        assert "Informe um valor inicial, um aporte mensal ou os dois" in html

    def test_only_own_products_are_offered(
        self, client: Client, user: Any, other_user: Any
    ) -> None:
        theirs = create_product(
            other_user, ProductData("Secreto", "cdi", Decimal(150), "taxed", True)
        )
        client.force_login(user)
        html = client.get(reverse("investments:simulator")).content.decode()
        assert "Secreto" not in html
        assert "100% do CDI (referência)" in html
        forged = client.get(
            reverse("investments:simulator"),
            {"inicial": "1.000,00", "meses": "12", "produto": [str(theirs.pk)]},
        ).content.decode()
        assert "Secreto" not in forged  # escolha inválida: não simula produto alheio
