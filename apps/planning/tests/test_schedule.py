import datetime

import pytest

from apps.planning.domain.schedule import (
    Frequency,
    describe,
    monthly_equivalent,
    next_occurrence,
    occurrences,
)

D = datetime.date
M, W, Y = Frequency.MONTHLY, Frequency.WEEKLY, Frequency.YEARLY


def test_monthly_end_of_month_does_not_drift() -> None:
    days = list(occurrences(D(2026, 1, 31), M, 1, through=D(2026, 5, 31)))
    assert days == [D(2026, 1, 31), D(2026, 2, 28), D(2026, 3, 31), D(2026, 4, 30), D(2026, 5, 31)]


def test_weekly_and_interval() -> None:
    assert list(occurrences(D(2026, 10, 2), W, 2, through=D(2026, 10, 31))) == [
        D(2026, 10, 2),
        D(2026, 10, 16),
        D(2026, 10, 30),
    ]
    assert list(occurrences(D(2026, 1, 10), M, 3, through=D(2026, 12, 31))) == [
        D(2026, 1, 10),
        D(2026, 4, 10),
        D(2026, 7, 10),
        D(2026, 10, 10),
    ]


def test_yearly_leap_day() -> None:
    assert list(occurrences(D(2028, 2, 29), Y, 1, through=D(2032, 12, 31))) == [
        D(2028, 2, 29),
        D(2029, 2, 28),
        D(2030, 2, 28),
        D(2031, 2, 28),
        D(2032, 2, 29),
    ]


def test_after_and_end_bounds() -> None:
    days = occurrences(
        D(2026, 1, 5), M, 1, after=D(2026, 3, 5), through=D(2026, 12, 31), end=D(2026, 6, 4)
    )
    assert list(days) == [D(2026, 4, 5), D(2026, 5, 5)]  # 05/03 exclusivo; 05/06 > fim


def test_invalid_interval() -> None:
    with pytest.raises(ValueError, match="interval"):
        list(occurrences(D(2026, 1, 1), M, 0, through=D(2026, 2, 1)))


def test_next_occurrence() -> None:
    assert next_occurrence(D(2026, 1, 5), M, 1, on_or_after=D(2026, 10, 5)) == D(2026, 10, 5)
    assert next_occurrence(D(2026, 1, 5), M, 1, on_or_after=D(2026, 10, 6)) == D(2026, 11, 5)
    assert next_occurrence(D(2026, 1, 5), Y, 1, on_or_after=D(2026, 10, 6)) == D(2027, 1, 5)
    assert (
        next_occurrence(D(2026, 1, 5), M, 1, on_or_after=D(2026, 10, 6), end=D(2026, 11, 1)) is None
    )


@pytest.mark.parametrize(
    ("start", "frequency", "interval", "text"),
    [
        (D(2026, 1, 5), M, 1, "Todo mês no dia 5"),
        (D(2026, 1, 31), M, 1, "Todo mês no dia 31 (ou no último)"),
        (D(2026, 1, 5), M, 2, "A cada 2 meses, no dia 5"),
        (D(2026, 10, 2), W, 1, "Toda semana (sexta)"),
        (D(2026, 10, 2), W, 2, "A cada 2 semanas (sexta)"),
        (D(2026, 3, 15), Y, 1, "Todo ano em 15/03"),
    ],
)
def test_describe(start: datetime.date, frequency: Frequency, interval: int, text: str) -> None:
    assert describe(start, frequency, interval) == text


def test_monthly_equivalent() -> None:
    assert monthly_equivalent(120_000, Y, 1) == 10_000
    assert monthly_equivalent(10_000, W, 1) == 43_333  # 52 semanas / 12 meses
    assert monthly_equivalent(30_000, M, 3) == 10_000
