"""Núcleo de investimentos: valores conferidos à mão (fórmulas e tabelas oficiais)."""

import datetime
from decimal import Decimal

import pytest

from apps.investments.domain.calendar import business_days, easter, holidays, is_business_day
from apps.investments.domain.portfolio import InsufficientFundsError, Portfolio
from apps.investments.domain.rates import (
    CdiPercent,
    Poupanca,
    Prefixed,
    annual_to_daily,
    full_months,
)
from apps.investments.domain.taxes import EXEMPT, TAXED, iof_rate, ir_rate

D = datetime.date


class TestCalendar:
    def test_easter_and_moving_holidays(self) -> None:
        assert easter(2026) == D(2026, 4, 5)
        assert easter(2027) == D(2027, 3, 28)
        h = holidays(2026)
        assert {D(2026, 2, 16), D(2026, 2, 17), D(2026, 4, 3), D(2026, 6, 4)} <= h
        assert D(2026, 11, 20) in h  # Consciência Negra (nacional desde 2024)
        assert D(2023, 11, 20) not in holidays(2023)

    def test_business_days(self) -> None:
        assert not is_business_day(D(2026, 10, 12))  # Nossa Senhora Aparecida (segunda)
        assert not is_business_day(D(2026, 10, 10))  # sábado
        assert business_days(D(2026, 10, 9), D(2026, 10, 14)) == 2  # sex 9 e ter 13
        assert business_days(D(2026, 10, 14), D(2026, 10, 14)) == 0
        # 2026: 261 dias de semana - 12 feriados em dias de semana.
        assert business_days(D(2026, 1, 1), D(2027, 1, 1)) == 249


class TestRates:
    def test_252_convention(self) -> None:
        daily = annual_to_daily(Decimal("0.10"))
        assert ((1 + daily) ** 252).quantize(Decimal("0.0000001")) == Decimal("1.1000000")

    def test_cdi_percent(self) -> None:
        start, end = D(2026, 1, 2), D(2027, 1, 4)  # ano útil "cheio"
        n = business_days(start, end)
        hundred = CdiPercent(Decimal("0.149"), Decimal(100)).factor(start, end)
        expected = (1 + annual_to_daily(Decimal("0.149"))) ** n
        assert hundred.quantize(Decimal("1e-12")) == expected.quantize(Decimal("1e-12"))
        boosted = CdiPercent(Decimal("0.149"), Decimal(120)).factor(start, end)
        assert boosted > hundred
        assert CdiPercent(Decimal("0.149"), Decimal(100)).describe() == "100% do CDI"

    def test_prefixed_over_252_days_matches_annual(self) -> None:
        start = D(2026, 1, 2)
        end = start
        while business_days(start, end) < 252:
            end += datetime.timedelta(days=1)
        factor = Prefixed(Decimal("0.12")).factor(start, end)
        assert factor.quantize(Decimal("0.000001")) == Decimal("1.120000")

    def test_poupanca_rules(self) -> None:
        assert Poupanca(Decimal("0.15")).monthly == Decimal("0.005")
        low = Poupanca(Decimal("0.07")).monthly  # 70% da Selic, mensalizada
        assert low.quantize(Decimal("0.000001")) == Decimal("0.003994")
        assert full_months(D(2026, 1, 15), D(2026, 3, 14)) == 1  # sacou antes do aniversário
        assert (
            Poupanca(Decimal("0.15")).factor(D(2026, 1, 15), D(2026, 3, 15))
            == Decimal("1.005") ** 2
        )


class TestTaxes:
    @pytest.mark.parametrize(
        ("days", "rate"), [(1, "0.96"), (10, "0.66"), (29, "0.03"), (30, "0"), (400, "0")]
    )
    def test_iof(self, days: int, rate: str) -> None:
        assert iof_rate(days) == Decimal(rate)

    @pytest.mark.parametrize(
        ("days", "rate"),
        [(180, "0.225"), (181, "0.20"), (360, "0.20"), (361, "0.175"), (721, "0.15")],
    )
    def test_ir(self, days: int, rate: str) -> None:
        assert ir_rate(days) == Decimal(rate)

    def test_ir_is_applied_after_iof(self) -> None:
        iof, ir = TAXED.taxes(Decimal(1000), 10)  # 66% de IOF, 22,5% de IR sobre o resto
        assert (iof, ir) == (Decimal(660), Decimal("76.5"))
        assert EXEMPT.taxes(Decimal(1000), 10) == (Decimal(0), Decimal(0))


class TestPortfolio:
    def test_lots_are_taxed_by_their_own_age(self) -> None:
        p = Portfolio(Prefixed(Decimal("0.12")), TAXED)
        p.deposit(D(2025, 1, 2), 100_000)
        p.deposit(D(2026, 9, 1), 100_000)
        v = p.value(D(2026, 10, 1))
        old_gain, new_gain = (
            100_000 * (Prefixed(Decimal("0.12")).factor(D(2025, 1, 2), D(2026, 10, 1)) - 1),
            100_000 * (Prefixed(Decimal("0.12")).factor(D(2026, 9, 1), D(2026, 10, 1)) - 1),
        )
        # 637 dias (até 720) → 17,5%; o aporte de 30 dias → 22,5%.
        expected_ir = old_gain * Decimal("0.175") + new_gain * Decimal("0.225")
        assert abs(v.ir_cents - float(expected_ir)) <= 1
        assert v.iof_cents == 0  # 30 dias: já sem IOF
        assert v.principal_cents == 200_000
        assert v.net_cents == v.gross_cents - v.ir_cents

    def test_withdraw_net_fifo(self) -> None:
        p = Portfolio(Prefixed(Decimal("0.12")), TAXED)
        p.deposit(D(2026, 1, 2), 100_000)
        p.deposit(D(2026, 6, 1), 100_000)
        before = p.value(D(2026, 10, 1))
        out = p.withdraw_net(D(2026, 10, 1), 50_000)
        assert out.net_cents == 50_000
        assert p.lots[0].day == D(2026, 1, 2)  # saiu só parte do lote mais antigo
        after = p.value(D(2026, 10, 1))
        assert abs(after.net_cents - (before.net_cents - 50_000)) <= 1
        with pytest.raises(InsufficientFundsError):
            p.withdraw_net(D(2026, 10, 1), 10_000_000)

    def test_same_day_withdraw_has_no_gain(self) -> None:
        p = Portfolio(CdiPercent(Decimal("0.149"), Decimal(100)), TAXED)
        p.deposit(D(2026, 10, 13), 10_000)
        out = p.withdraw_net(D(2026, 10, 13), 10_000)
        assert (out.gross_cents, out.iof_cents, out.ir_cents) == (10_000, 0, 0)
        assert p.lots == []


def test_business_index_matches_day_by_day_count() -> None:
    def naive(start: datetime.date, end: datetime.date) -> int:
        days, day = 0, start
        while day < end:
            days += is_business_day(day)
            day += datetime.timedelta(days=1)
        return days

    pairs = [
        (D(1998, 12, 30), D(2001, 1, 3)),
        (D(2026, 2, 13), D(2026, 2, 19)),
        (D(2026, 12, 31), D(2027, 1, 4)),
        (D(2030, 6, 1), D(2041, 6, 1)),
    ]
    for start, end in pairs:
        assert business_days(start, end) == naive(start, end)
