"""Cálculos financeiros puros (sem Django): totais, variações, médias e participações.

Valores em centavos (int). Percentuais em Decimal com 1 casa, arredondamento bancário, e
`None` quando a comparação não tem base (ex.: período anterior sem despesas) — a interface
deve dizer "sem base de comparação" em vez de inventar um número.
"""

import datetime
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal

ONE_DECIMAL = Decimal("0.1")


def percent(part: int, whole: int) -> Decimal | None:
    if whole == 0:
        return None
    return (Decimal(part) * 100 / Decimal(whole)).quantize(ONE_DECIMAL, ROUND_HALF_EVEN)


def pct_change(current: int, previous: int) -> Decimal | None:
    """Variação percentual; None se não houver base (anterior = 0)."""
    if previous == 0:
        return None
    return (Decimal(current - previous) * 100 / Decimal(abs(previous))).quantize(
        ONE_DECIMAL, ROUND_HALF_EVEN
    )


@dataclass(frozen=True)
class Totals:
    income_cents: int
    expense_cents: int

    @property
    def net_cents(self) -> int:
        return self.income_cents - self.expense_cents

    @property
    def savings_rate(self) -> Decimal | None:
        """Parcela das receitas que sobrou (resultado / receitas)."""
        return percent(self.net_cents, self.income_cents) if self.income_cents > 0 else None


@dataclass(frozen=True)
class Delta:
    """Variação de um indicador contra o período anterior equivalente."""

    current_cents: int
    previous_cents: int
    up_is_good: bool

    @property
    def pct(self) -> Decimal | None:
        return pct_change(self.current_cents, self.previous_cents)

    @property
    def direction(self) -> str:
        if self.current_cents > self.previous_cents:
            return "up"
        if self.current_cents < self.previous_cents:
            return "down"
        return "flat"

    @property
    def is_good(self) -> bool | None:
        if self.direction == "flat":
            return None
        return (self.direction == "up") == self.up_is_good


def daily_average(total_cents: int, days: int) -> int:
    return round(total_cents / days) if days > 0 else 0


def monthly_average(values: Sequence[int]) -> int | None:
    """Média dos meses fechados informados; None sem meses."""
    return round(sum(values) / len(values)) if values else None


@dataclass(frozen=True)
class Share:
    key: str
    cents: int
    pct: Decimal | None


def shares(items: Iterable[tuple[str, int]]) -> list[Share]:
    """Participação de cada item no total, maior primeiro."""
    materialized = sorted(items, key=lambda item: item[1], reverse=True)
    total = sum(cents for _, cents in materialized)
    return [Share(key, cents, percent(cents, total)) for key, cents in materialized]


def cumulative(
    daily: dict[datetime.date, int], start: datetime.date, end: datetime.date
) -> list[int]:
    """Soma acumulada dia a dia de `start` a `end` (dias sem lançamento repetem o valor)."""
    running, series = 0, []
    day = start
    while day <= end:
        running += daily.get(day, 0)
        series.append(running)
        day += datetime.timedelta(days=1)
    return series
