"""Estratégias de compra (domínio): parcelas, juros embutidos e custo na mesma data-base."""

import datetime
from decimal import Decimal

import pytest

from apps.investments.domain.purchase import (
    Payment,
    Strategy,
    compare,
    implied_monthly_rate,
    installments,
    split_installments,
)
from apps.investments.domain.rates import CdiPercent, Prefixed
from apps.investments.domain.taxes import EXEMPT, TAXED

D = datetime.date
START = D(2026, 10, 14)
NO_YIELD = Prefixed(Decimal(0))


def cash(price: int, discount: str = "0") -> Strategy:
    value = round(price * (1 - Decimal(discount) / 100))
    return Strategy("avista", "À vista", (Payment(START, value),))


def parcelado(price: int, n: int, first: int = 1) -> Strategy:
    return Strategy(
        "sem-juros", f"{n}x", tuple(split_installments(START, price, n, first_in_months=first))
    )


def test_installment_schedules() -> None:
    assert [p.day for p in installments(START, 3, 100, first_in_months=0)] == [
        D(2026, 10, 14),
        D(2026, 11, 14),
        D(2026, 12, 14),
    ]
    split = split_installments(START, 100_000, 3)
    assert [p.cents for p in split] == [33_334, 33_333, 33_333]
    assert split[0].day == D(2026, 11, 14)
    s = Strategy("x", "x", (Payment(D(2026, 11, 1), 10), Payment(D(2026, 11, 20), 5)))
    assert (s.max_month_cents, s.months_committed) == (15, 1)


def test_implied_rate() -> None:
    # R$ 1.000 à vista ou 12 x R$ 100: juros ≈ 2,92% a.m. (Price, 1ª parcela em 30 dias).
    rate = implied_monthly_rate(100_000, installments(START, 12, 10_000), START)
    assert rate is not None
    assert rate.quantize(Decimal("0.0001")) == Decimal("0.0292")
    assert implied_monthly_rate(100_000, split_installments(START, 100_000, 10), START) is None


def test_without_yield_interest_free_ties_and_wins_on_monthly_impact() -> None:
    result = compare(
        [cash(100_000), parcelado(100_000, 10)],
        yield_=NO_YIELD,
        taxes=EXEMPT,
        start=START,
        cash_price_cents=100_000,
    )
    assert [r.cost_cents for r in result.results] == [100_000, 100_000]
    assert result.best.strategy.key == "sem-juros"  # mesmo custo, pesa menos por mês


def test_money_invested_makes_installments_cheaper() -> None:
    result = compare(
        [cash(100_000), parcelado(100_000, 10)],
        yield_=CdiPercent(Decimal("0.1365"), Decimal(100)),
        taxes=TAXED,
        start=START,
        cash_price_cents=100_000,
    )
    best, worst = result.results
    assert best.strategy.key == "sem-juros"
    assert abs(worst.cost_cents - 100_000) <= 1  # à vista sem desconto: exatamente o preço
    assert 94_000 < best.cost_cents < 98_000  # o dinheiro rendeu enquanto esperava as parcelas
    assert worst.extra_vs_best_cents == worst.cost_cents - best.cost_cents > 0
    assert result.base_day == D(2027, 8, 14)


def test_big_discount_beats_installments() -> None:
    result = compare(
        [cash(100_000, "10"), parcelado(100_000, 10)],
        yield_=CdiPercent(Decimal("0.1365"), Decimal(100)),
        taxes=TAXED,
        start=START,
        cash_price_cents=90_000,
    )
    assert result.best.strategy.key == "avista"  # 10% de desconto rende mais que o CDI


def test_interest_installments_show_rate_and_lose() -> None:
    juros = Strategy("com-juros", "12x com juros", tuple(installments(START, 12, 10_000)))
    result = compare(
        [cash(100_000), juros],
        yield_=CdiPercent(Decimal("0.1365"), Decimal(100)),
        taxes=TAXED,
        start=START,
        cash_price_cents=100_000,
    )
    loser = result.results[-1]
    assert loser.strategy.key == "com-juros"
    assert loser.implied_rate is not None
    assert loser.implied_rate > Decimal("0.02")


def test_requires_strategies() -> None:
    with pytest.raises(ValueError, match="nenhuma"):
        compare([], yield_=NO_YIELD, taxes=EXEMPT, start=START, cash_price_cents=1)
