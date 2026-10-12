import datetime
from decimal import Decimal

import pytest

from apps.analytics.domain.metrics import (
    Delta,
    Totals,
    cumulative,
    daily_average,
    monthly_average,
    pct_change,
    percent,
    shares,
)
from apps.analytics.domain.periods import (
    DateRange,
    add_months,
    comparable_ranges,
    month_end,
    months_between,
)

D = datetime.date


class TestPeriods:
    @pytest.mark.parametrize(
        ("day", "months", "expected"),
        [
            (D(2026, 3, 31), -1, D(2026, 2, 28)),
            (D(2024, 3, 31), -1, D(2024, 2, 29)),
            (D(2026, 1, 15), -1, D(2025, 12, 15)),
            (D(2026, 11, 30), 3, D(2027, 2, 28)),
        ],
    )
    def test_add_months(self, day: datetime.date, months: int, expected: datetime.date) -> None:
        assert add_months(day, months) == expected

    def test_months_between(self) -> None:
        assert months_between(D(2025, 11, 20), D(2026, 2, 1)) == [
            D(2025, 11, 1),
            D(2025, 12, 1),
            D(2026, 1, 1),
            D(2026, 2, 1),
        ]

    def test_current_month_is_prorated(self) -> None:
        c = comparable_ranges(D(2026, 10, 1), D(2026, 10, 31), today=D(2026, 10, 10))
        assert c is not None
        assert c.current == DateRange(D(2026, 10, 1), D(2026, 10, 10))
        assert c.previous == DateRange(D(2026, 9, 1), D(2026, 9, 10))
        assert c.partial

    def test_prorated_clamps_to_shorter_previous_month(self) -> None:
        c = comparable_ranges(D(2026, 3, 1), D(2026, 3, 31), today=D(2026, 3, 31))
        assert c is not None
        assert c.previous == DateRange(D(2026, 2, 1), D(2026, 2, 28))
        assert not c.partial

    def test_closed_month_compares_whole_previous_month(self) -> None:
        c = comparable_ranges(D(2026, 9, 1), D(2026, 9, 30), today=D(2026, 10, 10))
        assert c is not None
        assert c.previous == DateRange(D(2026, 8, 1), D(2026, 8, 31))

    def test_multi_month_span(self) -> None:
        c = comparable_ranges(D(2026, 8, 1), D(2026, 10, 31), today=D(2026, 10, 10))
        assert c is not None
        assert c.previous.start == D(2026, 5, 1)
        assert c.previous.end == D(2026, 7, 10)  # mesmo número de dias decorridos

    def test_rolling_window_shifts_by_length(self) -> None:
        c = comparable_ranges(D(2026, 9, 11), D(2026, 10, 10), today=D(2026, 10, 10))
        assert c is not None
        assert c.previous == DateRange(D(2026, 8, 12), D(2026, 9, 10))

    def test_future_period_has_no_comparison(self) -> None:
        assert comparable_ranges(D(2026, 11, 1), D(2026, 11, 30), today=D(2026, 10, 10)) is None

    def test_month_end(self) -> None:
        assert month_end(D(2028, 2, 3)) == D(2028, 2, 29)


class TestMetrics:
    def test_percent_and_change(self) -> None:
        assert percent(1, 3) == Decimal("33.3")
        assert percent(5, 0) is None
        assert pct_change(118, 100) == Decimal("18.0")
        assert pct_change(50, 0) is None
        assert pct_change(-50, -100) == Decimal("50.0")  # resultado negativo melhorou

    def test_totals(self) -> None:
        t = Totals(income_cents=500_000, expense_cents=400_000)
        assert t.net_cents == 100_000
        assert t.savings_rate == Decimal("20.0")
        assert Totals(0, 1_000).savings_rate is None

    @pytest.mark.parametrize(
        ("current", "previous", "up_is_good", "direction", "good"),
        [
            (120, 100, False, "up", False),  # despesa subiu: ruim
            (80, 100, False, "down", True),
            (120, 100, True, "up", True),
            (100, 100, True, "flat", None),
        ],
    )
    def test_delta(
        self, current: int, previous: int, up_is_good: bool, direction: str, good: bool | None
    ) -> None:
        delta = Delta(current, previous, up_is_good)
        assert (delta.direction, delta.is_good) == (direction, good)

    def test_averages(self) -> None:
        assert daily_average(10_000, 3) == 3_333
        assert daily_average(10_000, 0) == 0
        assert monthly_average([100, 200, 300]) == 200
        assert monthly_average([]) is None

    def test_shares_sorted_and_sum_to_100(self) -> None:
        result = shares([("a", 100), ("b", 300), ("c", 600)])
        assert [s.key for s in result] == ["c", "b", "a"]
        assert sum(s.pct or 0 for s in result) == Decimal("100.0")

    def test_cumulative_fills_gaps(self) -> None:
        daily = {D(2026, 10, 1): 100, D(2026, 10, 3): 50}
        assert cumulative(daily, D(2026, 10, 1), D(2026, 10, 4)) == [100, 100, 150, 150]
