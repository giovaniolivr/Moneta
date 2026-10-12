"""Estratégias de compra: qual forma de pagar pesa menos no bolso (Python puro).

Método: em todas as estratégias, o MESMO capital começa aplicado no produto onde o dinheiro
está. Cada pagamento é resgatado na data em que vence (com IOF/IR de verdade, PEPS). Todas são
avaliadas na mesma data-base — a do último pagamento entre elas. Custo real = quanto o
patrimônio fica menor nessa data em relação a não comprar, trazido para o dinheiro de hoje
pelo próprio rendimento líquido do produto (à vista sem desconto custa exatamente o preço).
Menor custo = melhor.
"""

import datetime
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from apps.analytics.domain.periods import add_months

from .portfolio import Portfolio
from .rates import Yield
from .taxes import TaxRule


@dataclass(frozen=True)
class Payment:
    day: datetime.date
    cents: int


@dataclass(frozen=True)
class Strategy:
    key: str
    label: str
    payments: tuple[Payment, ...]

    @property
    def total_cents(self) -> int:
        return sum(p.cents for p in self.payments)

    @property
    def last_day(self) -> datetime.date:
        return max(p.day for p in self.payments)

    @property
    def max_month_cents(self) -> int:
        """Maior desembolso somado num mesmo mês (o impacto no orçamento)."""
        by_month: dict[tuple[int, int], int] = defaultdict(int)
        for p in self.payments:
            by_month[(p.day.year, p.day.month)] += p.cents
        return max(by_month.values())

    @property
    def months_committed(self) -> int:
        return len({(p.day.year, p.day.month) for p in self.payments})


def installments(
    start: datetime.date, count: int, value_cents: int, *, first_in_months: int = 1
) -> list[Payment]:
    """`count` parcelas iguais, a 1ª daqui a `first_in_months` meses (0 = no ato)."""
    return [Payment(add_months(start, first_in_months + k), value_cents) for k in range(count)]


def split_installments(
    start: datetime.date, total_cents: int, count: int, *, first_in_months: int = 1
) -> list[Payment]:
    """Divide um total em parcelas (centavos que sobram na 1ª, como os cartões)."""
    base, remainder = divmod(total_cents, count)
    payments = installments(start, count, base, first_in_months=first_in_months)
    first = payments[0]
    return [Payment(first.day, first.cents + remainder), *payments[1:]]


def _month_offset(start: datetime.date, day: datetime.date) -> int:
    return (day.year - start.year) * 12 + day.month - start.month


def implied_monthly_rate(
    cash_price_cents: int, payments: Sequence[Payment], start: datetime.date
) -> Decimal | None:
    """Juros mensais embutidos: a taxa que faz o valor presente das parcelas igualar o preço
    à vista. None se as parcelas não somam mais que o preço (não há juros a cobrar)."""
    if sum(p.cents for p in payments) <= cash_price_cents:
        return None
    offsets = [(_month_offset(start, p.day), Decimal(p.cents)) for p in payments]

    def present_value(rate: Decimal) -> Decimal:
        return sum((cents / (1 + rate) ** k for k, cents in offsets), Decimal(0))

    low, high = Decimal(0), Decimal(1)  # até 100% a.m.
    target = Decimal(cash_price_cents)
    for _ in range(80):
        mid = (low + high) / 2
        if present_value(mid) > target:
            low = mid
        else:
            high = mid
    return (low + high) / 2


@dataclass(frozen=True)
class StrategyResult:
    strategy: Strategy
    final_net_cents: int  # patrimônio líquido na data-base, depois de pagar a compra
    cost_cents: int  # quanto ficou mais pobre, em dinheiro de hoje (vs. não comprar)
    extra_vs_best_cents: int
    implied_rate: Decimal | None  # juros embutidos (a.m.) vs. o preço à vista


@dataclass(frozen=True)
class Comparison:
    base_day: datetime.date
    capital_cents: int
    results: list[StrategyResult]  # do menor para o maior custo

    @property
    def best(self) -> StrategyResult:
        return self.results[0]


def compare(
    strategies: Sequence[Strategy],
    *,
    yield_: Yield,
    taxes: TaxRule,
    start: datetime.date,
    cash_price_cents: int,
) -> Comparison:
    if not strategies:
        raise ValueError("nenhuma estratégia")
    base_day = max(s.last_day for s in strategies)
    capital = max(s.total_cents for s in strategies)
    untouched = Portfolio(yield_, taxes)
    untouched.deposit(start, capital)
    baseline = untouched.value(base_day).net_cents

    partial = []
    for strategy in strategies:
        portfolio = Portfolio(yield_, taxes)
        portfolio.deposit(start, capital)
        for payment in sorted(strategy.payments, key=lambda p: p.day):
            portfolio.withdraw_net(payment.day, payment.cents)
        final = portfolio.value(base_day).net_cents
        rate = implied_monthly_rate(cash_price_cents, strategy.payments, start)
        # Desconta pelo crescimento líquido do próprio capital até a data-base.
        cost_today = round(Decimal(baseline - final) * capital / baseline) if baseline else 0
        partial.append((strategy, final, cost_today, rate))
    # Empate de custo: a que pesa menos num mês só vem primeiro.
    partial.sort(key=lambda item: (item[2], item[0].max_month_cents))
    best_cost = partial[0][2]
    results = [
        StrategyResult(strategy, final, cost, cost - best_cost, rate)
        for strategy, final, cost, rate in partial
    ]
    return Comparison(base_day, capital, results)
