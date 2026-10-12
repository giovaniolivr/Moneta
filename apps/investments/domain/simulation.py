"""Simulação de investimento: valor inicial + aportes mensais até um prazo (Python puro)."""

import datetime
from dataclasses import dataclass
from decimal import Decimal

from apps.analytics.domain.periods import add_months

from .portfolio import Portfolio, Valuation
from .rates import Yield
from .taxes import TaxRule

MAX_MONTHS = 600  # 50 anos


@dataclass(frozen=True)
class Point:
    day: datetime.date
    invested_cents: int
    valuation: Valuation


@dataclass(frozen=True)
class Simulation:
    points: list[Point]  # um por mês (ou por ano em prazos longos), o último é o resgate

    @property
    def final(self) -> Point:
        return self.points[-1]

    def real_value(self, inflation_annual: Decimal | None) -> int | None:
        """Líquido final em dinheiro de hoje (descontada a inflação anual informada)."""
        if inflation_annual is None:
            return None
        years = Decimal((self.final.day - self.points[0].day).days) / Decimal(365)
        deflator = (1 + inflation_annual) ** years
        return int((Decimal(self.final.valuation.net_cents) / deflator).to_integral_value())


def simulate(
    *,
    yield_: Yield,
    taxes: TaxRule,
    start: datetime.date,
    initial_cents: int,
    monthly_cents: int,
    months: int,
) -> Simulation:
    """Valor inicial e o 1º aporte em `start`; demais aportes no mesmo dia dos meses seguintes
    (12 meses = 12 aportes); resgate de tudo no mês `months`. Pontos mensais até 10 anos;
    acima disso, anuais (o último ponto é sempre o resgate)."""
    if not 1 <= months <= MAX_MONTHS:
        raise ValueError(f"prazo entre 1 e {MAX_MONTHS} meses")
    step = 1 if months <= 120 else 12
    portfolio = Portfolio(yield_, taxes)
    portfolio.deposit(start, initial_cents + monthly_cents)
    invested = initial_cents + monthly_cents
    points = [Point(start, invested, portfolio.value(start))]
    for month in range(1, months + 1):
        day = add_months(start, month)
        if month % step == 0 or month == months:
            points.append(Point(day, invested, portfolio.value(day)))
        if month < months:
            portfolio.deposit(day, monthly_cents)
            invested += monthly_cents
    return Simulation(points)
