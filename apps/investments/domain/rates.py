"""Rentabilidade: fator de crescimento de um valor entre duas datas (Python puro).

Convenções do mercado brasileiro:
- Taxas anuais em base 252 dias úteis: diária = (1 + anual)^(1/252) - 1.
- "X% do CDI": a taxa DI diária é multiplicada por X% a cada dia útil.
- Poupança: rende no aniversário mensal (0,5% a.m. + TR se Selic > 8,5% a.a.; senão 70% da
  Selic). TR é tratada como zero (estimativa) — saques antes do aniversário perdem o mês.
Taxas em Decimal (0.149 = 14,9% a.a.).
"""

import datetime
from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Protocol

from .calendar import business_days

ONE = Decimal(1)
POUPANCA_SELIC_THRESHOLD = Decimal("0.085")


def annual_to_daily(annual: Decimal) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 34
        return (ONE + annual) ** (ONE / Decimal(252)) - ONE


def annual_to_monthly(annual: Decimal) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 34
        return (ONE + annual) ** (ONE / Decimal(12)) - ONE


def _compound(daily: Decimal, periods: int) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 34
        return (ONE + daily) ** periods


class Yield(Protocol):
    def factor(self, start: datetime.date, end: datetime.date) -> Decimal: ...

    def describe(self) -> str: ...


@dataclass(frozen=True)
class CdiPercent:
    """Pós-fixado: `percent`% do CDI (ex.: Caixinha, CDB, LCI)."""

    cdi_annual: Decimal
    percent: Decimal  # 100 = 100% do CDI

    def factor(self, start: datetime.date, end: datetime.date) -> Decimal:
        daily = annual_to_daily(self.cdi_annual) * self.percent / 100
        return _compound(daily, business_days(start, end))

    def describe(self) -> str:
        return f"{self.percent.normalize():f}% do CDI"


@dataclass(frozen=True)
class Prefixed:
    """Prefixado: taxa anual fixa em base 252."""

    annual: Decimal

    def factor(self, start: datetime.date, end: datetime.date) -> Decimal:
        return _compound(annual_to_daily(self.annual), business_days(start, end))

    def describe(self) -> str:
        return f"{(self.annual * 100).normalize():f}% a.a. prefixado"


def full_months(start: datetime.date, end: datetime.date) -> int:
    """Aniversários mensais completos entre as datas (regra da poupança)."""
    months = (end.year - start.year) * 12 + end.month - start.month
    if end.day < start.day:
        months -= 1
    return max(months, 0)


@dataclass(frozen=True)
class Poupanca:
    selic_annual: Decimal

    @property
    def monthly(self) -> Decimal:
        if self.selic_annual > POUPANCA_SELIC_THRESHOLD:
            return Decimal("0.005")
        return annual_to_monthly(self.selic_annual * Decimal("0.7"))

    def factor(self, start: datetime.date, end: datetime.date) -> Decimal:
        return _compound(self.monthly, full_months(start, end))

    def describe(self) -> str:
        return "Poupança"
