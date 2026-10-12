"""Regras de análise automática: cada uma com caso positivo, negativo e de dados insuficientes."""

import datetime
import uuid
from dataclasses import replace
from decimal import Decimal
from typing import Any

from apps.analytics.domain.insights import (
    AboveAverageRule,
    AnalyticsContext,
    ConcentrationRule,
    Evidence,
    Insight,
    MonthData,
    MonthSummaryRule,
    TrendRule,
    WeekdayRule,
    evaluate,
    weekday_window,
)
from apps.analytics.domain.metrics import Totals
from apps.analytics.domain.periods import DateRange, first_full_month

D = datetime.date
TODAY = D(2026, 10, 15)
FOOD, HOME, FUN, CAR = (uuid.uuid4() for _ in range(4))
NAMES = {FOOD: "Alimentação", HOME: "Moradia", FUN: "Lazer", CAR: "Transporte"}


def month(
    day: datetime.date,
    by_category: dict[uuid.UUID, int],
    comparable: dict[uuid.UUID, int] | None = None,
) -> MonthData:
    return MonthData(
        day,
        income_cents=500_000,
        expense_cents=sum(by_category.values()),
        by_category=by_category,
        comparable_by_category=by_category if comparable is None else comparable,
    )


def ctx(**overrides: Any) -> AnalyticsContext:
    """Outubro em andamento (1-15), comparado com 1-15 de setembro."""
    base = AnalyticsContext(
        today=TODAY,
        current=DateRange(D(2026, 10, 1), D(2026, 10, 15)),
        partial=True,
        single_month=True,
        totals=Totals(500_000, 200_000),
        previous=DateRange(D(2026, 9, 1), D(2026, 9, 15)),
        previous_totals=Totals(500_000, 250_000),
        expenses_by_category={HOME: 120_000, FOOD: 50_000, FUN: 20_000, CAR: 10_000},
        category_names=NAMES,
        months=[],
        first_date=D(2026, 1, 1),
    )
    return replace(base, **overrides)


def evidence(insight: Insight) -> dict[str, Any]:
    return {e.label: e.value for e in insight.evidence}


class TestSummary:
    def test_partial_month_compares_same_days(self) -> None:
        [insight] = MonthSummaryRule().evaluate(ctx())
        assert insight.severity == "positive"  # sobrou dinheiro e gastou menos
        assert insight.message == (
            "Em outubro até o dia 15, você gastou R$\xa02.000,00, 20% a menos que nos mesmos "
            "dias de setembro. Sobraram R$\xa03.000,00 (60% das receitas)."
        )
        ev = evidence(insight)
        assert ev["Despesas de 01/09/2026 a 15/09/2026"] == 250_000
        assert ev["Variação das despesas"] == Decimal("-20.0")
        assert ev["Taxa de poupança"] == Decimal("60.0")
        assert insight.filter is not None
        assert insight.filter.kinds == ("income", "expense")
        assert insight.filter.date_to == D(2026, 10, 15)

    def test_closed_month_deficit_is_attention(self) -> None:
        closed = ctx(
            current=DateRange(D(2026, 9, 1), D(2026, 9, 30)),
            partial=False,
            previous=DateRange(D(2026, 8, 1), D(2026, 8, 31)),
            totals=Totals(300_000, 400_000),
            previous_totals=Totals(300_000, 400_000),
        )
        [insight] = MonthSummaryRule().evaluate(closed)
        assert insight.severity == "attention"
        assert insight.message == (
            "Em setembro, você gastou R$\xa04.000,00. "
            "As despesas superaram as receitas em R$\xa01.000,00."
        )

    def test_partial_month_deficit_only_informs(self) -> None:
        [insight] = MonthSummaryRule().evaluate(ctx(totals=Totals(0, 50_000)))
        assert insight.severity == "info"
        assert "Até agora, as despesas superam as receitas" in insight.message

    def test_no_previous_base(self) -> None:
        [insight] = MonthSummaryRule().evaluate(ctx(previous_totals=Totals(0, 0)))
        assert "menos que" not in insight.message
        assert "Variação das despesas" not in evidence(insight)

    def test_nothing_without_data(self) -> None:
        assert MonthSummaryRule().evaluate(ctx(totals=Totals(0, 0))) == []


class TestConcentration:
    def test_smallest_group_reaching_half(self) -> None:
        [insight] = ConcentrationRule().evaluate(ctx())
        assert (
            insight.message
            == "Moradia sozinha responde por 60% das despesas em outubro até o dia 15."
        )
        assert insight.filter is not None
        assert insight.filter.category_ids == (HOME,)
        assert evidence(insight)["Participação da categoria"] == Decimal("60.0")

    def test_several_categories(self) -> None:
        spread = {HOME: 40_000, FOOD: 30_000} | {uuid.uuid4(): 5_000 for _ in range(6)}
        [insight] = ConcentrationRule().evaluate(ctx(expenses_by_category=spread))
        assert insight.message.startswith("Moradia e Alimentação concentram 70%")
        assert insight.filter is not None
        assert insight.filter.category_ids == (HOME, FOOD)

    def test_not_reported_when_trivial_or_spread(self) -> None:
        assert ConcentrationRule().evaluate(ctx(expenses_by_category={HOME: 1_000})) == []
        # As 3 maiores não chegam a 50% → nada a dizer.
        ids = [uuid.uuid4() for _ in range(8)]
        even = dict.fromkeys(ids, 1_000)
        assert ConcentrationRule().evaluate(ctx(expenses_by_category=even)) == []
        # Fatias iguais: 2 de 3 categorias somarem 66,7% não é concentração.
        three = {HOME: 2_000, FOOD: 2_000, FUN: 2_000}
        assert ConcentrationRule().evaluate(ctx(expenses_by_category=three)) == []


def history(comparable_food: list[int], full_food: list[int] | None = None) -> list[MonthData]:
    """Julho, agosto e setembro completos."""
    full = full_food or [v * 2 for v in comparable_food]
    return [
        month(D(2026, m, 1), {FOOD: f}, {FOOD: c})
        for m, c, f in zip((7, 8, 9), comparable_food, full, strict=True)
    ]


class TestAboveAverage:
    def test_uses_same_days_of_previous_months(self) -> None:
        # Base pro-rata: 1-15 de cada mês = 30.000 em média (o mês inteiro seria 60.000).
        context = ctx(months=history([30_000, 30_000, 30_000]))
        [insight] = AboveAverageRule().evaluate(context)
        assert insight.severity == "attention"
        assert insight.message == (
            "Alimentação: R$\xa0500,00 em outubro até o dia 15, 66,7% acima da média dos mesmos "
            "dias (1 a 15) nos 3 meses anteriores (R$\xa0300,00)."
        )
        ev = evidence(insight)
        assert ev["Julho"] == 30_000
        assert ev["Diferença"] == 20_000
        assert insight.filter is not None
        assert insight.filter.category_ids == (FOOD,)

    def test_closed_month_uses_whole_months(self) -> None:
        context = ctx(
            current=DateRange(D(2026, 10, 1), D(2026, 10, 31)),
            partial=False,
            months=history([30_000] * 3, [40_000] * 3),
        )
        [insight] = AboveAverageRule().evaluate(context)
        assert "25% acima da média dos 3 meses anteriores (R$\xa0400,00)" in insight.message

    def test_thresholds_avoid_noise(self) -> None:
        # +20%, mas só R$ 10 a mais: abaixo do mínimo em reais.
        small = ctx(expenses_by_category={FOOD: 6_000}, months=history([5_000] * 3))
        assert AboveAverageRule().evaluate(small) == []
        # +10% (abaixo do limiar percentual) com diferença grande.
        mild = ctx(expenses_by_category={FOOD: 110_000}, months=history([100_000] * 3))
        assert AboveAverageRule().evaluate(mild) == []
        # Categoria nova (média zero) não tem base de comparação.
        new = ctx(expenses_by_category={FUN: 90_000}, months=history([30_000] * 3))
        assert AboveAverageRule().evaluate(new) == []

    def test_insufficient_history(self) -> None:
        [insight] = AboveAverageRule().evaluate(ctx(months=history([1, 1, 1])[1:]))
        assert insight.insufficient
        assert "3 meses completos" in insight.message
        assert "(há 2)" in insight.message

    def test_too_early_in_month(self) -> None:
        early = ctx(current=DateRange(D(2026, 10, 1), D(2026, 10, 3)), months=history([1] * 3))
        [insight] = AboveAverageRule().evaluate(early)
        assert insight.insufficient

    def test_reference_month_is_not_its_own_baseline(self) -> None:
        # Período fechado (setembro): setembro está em `months`, mas não entra na média.
        months = [
            month(D(2026, m, 1), {FOOD: cents})
            for m, cents in ((6, 30_000), (7, 30_000), (8, 30_000), (9, 300_000))
        ]
        context = ctx(
            current=DateRange(D(2026, 9, 1), D(2026, 9, 30)),
            partial=False,
            expenses_by_category={FOOD: 90_000},
            months=months,
        )
        [insight] = AboveAverageRule().evaluate(context)
        assert evidence(insight)["Média dos 3 meses anteriores"] == 30_000  # jun, jul, ago

    def test_only_single_month_periods(self) -> None:
        assert AboveAverageRule().evaluate(ctx(single_month=False, months=history([1] * 3))) == []


def series(values: dict[uuid.UUID, list[int]]) -> list[MonthData]:
    count = len(next(iter(values.values())))
    return [
        month(D(2026, 6 + i, 1), {cid: v[i] for cid, v in values.items()}) for i in range(count)
    ]


class TestTrend:
    def test_total_and_category_rising(self) -> None:
        context = ctx(months=series({FOOD: [10_000, 20_000, 30_000, 40_000], HOME: [100_000] * 4}))
        total, food = TrendRule().evaluate(context)
        assert total.message == (
            "Suas despesas subiram 3 meses seguidos: de R$\xa01.100,00 em junho para "
            "R$\xa01.400,00 em setembro (+27,3%)."
        )
        assert total.severity == "attention"
        assert food.message.startswith("Os gastos com Alimentação subiram 3 meses seguidos")
        assert food.filter is not None
        assert food.filter.category_ids == (FOOD,)
        assert food.filter.date_from == D(2026, 6, 1)
        assert food.filter.date_to == D(2026, 9, 30)

    def test_falling_is_positive(self) -> None:
        context = ctx(months=series({FOOD: [40_000, 30_000, 20_000, 10_000]}))
        insights = TrendRule().evaluate(context)
        assert {i.severity for i in insights} == {"positive"}

    def test_needs_consecutive_moves_and_minimum(self) -> None:
        zigzag = ctx(months=series({FOOD: [10_000, 30_000, 20_000, 40_000]}))
        assert TrendRule().evaluate(zigzag) == []
        tiny = ctx(months=series({FOOD: [100, 200, 300, 400]}))
        assert TrendRule().evaluate(tiny) == []

    def test_insufficient_history(self) -> None:
        [insight] = TrendRule().evaluate(ctx(months=series({FOOD: [1, 2, 3]})))
        assert insight.insufficient
        assert "4 meses completos" in insight.message


WINDOW = weekday_window(DateRange(D(2026, 10, 1), D(2026, 10, 15)), TODAY, 8)


class TestWeekday:
    def daily(self, weekday_cents: int, other_cents: int) -> dict[datetime.date, int]:
        days: dict[datetime.date, int] = {}
        day = WINDOW.start
        while day <= WINDOW.end:
            days[day] = weekday_cents if day.weekday() == 4 else other_cents
            day += datetime.timedelta(days=1)
        return days

    def test_window_ends_yesterday(self) -> None:
        assert (WINDOW.start, WINDOW.end) == (D(2026, 8, 20), D(2026, 10, 14))
        assert WINDOW.days == 56

    def test_typical_day_well_above_others(self) -> None:
        context = ctx(weekday_window=WINDOW, daily_expenses=self.daily(15_000, 4_000))
        [insight] = WeekdayRule().evaluate(context)
        assert insight.message == (
            "Você costuma gastar mais às sextas-feiras: R$\xa0150,00 num dia típico (mediana das "
            "últimas 8 semanas), contra R$\xa040,00 nos outros dias."
        )
        assert evidence(insight)["Dia típico: sexta-feira"] == 15_000

    def test_single_big_expense_is_not_a_pattern(self) -> None:
        daily = self.daily(4_000, 4_000)
        daily[D(2026, 9, 4)] = 300_000  # uma sexta com aluguel
        assert WeekdayRule().evaluate(ctx(weekday_window=WINDOW, daily_expenses=daily)) == []

    def test_insufficient_history(self) -> None:
        context = ctx(weekday_window=WINDOW, first_date=D(2026, 9, 1))
        [insight] = WeekdayRule().evaluate(context)
        assert insight.insufficient


def test_evaluate_orders_summary_alerts_then_pending() -> None:
    context = ctx(months=history([30_000] * 3))
    insights = evaluate(context)
    rule_ids = [i.rule_id for i in insights]
    assert rule_ids[0] == "resumo"
    assert rule_ids[1] == "acima_da_media"
    assert insights[-1].insufficient  # tendência: só 3 meses
    assert all(isinstance(e, Evidence) for i in insights for e in i.evidence)


def test_first_full_month() -> None:
    assert first_full_month(D(2026, 5, 1)) == D(2026, 5, 1)
    assert first_full_month(D(2026, 5, 2)) == D(2026, 6, 1)
    assert first_full_month(D(2026, 12, 20)) == D(2027, 1, 1)
