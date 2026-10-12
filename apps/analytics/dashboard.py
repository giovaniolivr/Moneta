"""Monta os dados da dashboard (indicadores, séries e detalhamentos) para um período."""

import datetime
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.urls import reverse

from apps.ledger.filters import TransactionFilter
from apps.ledger.models import TransactionKind

from . import selectors
from .domain.insights import (
    AnalyticsContext,
    BudgetSignal,
    Insight,
    InsightSettings,
    MonthData,
    evaluate,
    weekday_window,
)
from .domain.metrics import Delta, Totals, cumulative, daily_average, monthly_average, percent
from .domain.periods import (
    Comparison,
    add_months,
    comparable_ranges,
    first_full_month,
    month_end,
    month_start,
    months_between,
)

PERIODS: list[tuple[str, str]] = [
    ("mes", "Este mês"),
    ("mes-anterior", "Mês anterior"),
    ("3m", "Últimos 3 meses"),
    ("12m", "Últimos 12 meses"),
    ("ano", "Este ano"),
]
DEFAULT_PERIOD = "mes"
MONTH_PERIOD = "mes-especifico"  # mês escolhido pela navegação ◂ ▸ (nenhuma aba ativa)
HISTORY_MONTHS = 12  # histórico máximo usado pelas análises automáticas
MONTH_ABBR = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]


def period_bounds(period: str, today: datetime.date) -> tuple[datetime.date, datetime.date]:
    current_month = month_start(today)
    match period:
        case "mes-anterior":
            start = add_months(current_month, -1)
            return start, month_end(start)
        case "3m":
            return add_months(current_month, -2), month_end(today)
        case "12m":
            return add_months(current_month, -11), month_end(today)
        case "ano":
            return today.replace(month=1, day=1), today.replace(month=12, day=31)
        case _:
            return current_month, month_end(today)


def month_label(day: datetime.date, *, with_year: bool = True) -> str:
    label = MONTH_ABBR[day.month - 1]
    return f"{label}/{day:%y}" if with_year else label


@dataclass(frozen=True)
class Kpi:
    key: str
    label: str
    cents: int
    delta: Delta | None = None
    hint: str = ""


@dataclass(frozen=True)
class Dashboard:
    period: str
    start: datetime.date
    end: datetime.date
    comparison: Comparison | None
    totals: Totals
    kpis: list[Kpi]
    savings_rate: Decimal | None
    daily_average_cents: int
    monthly_average_cents: int | None
    closed_months: int
    categories: list[dict[str, Any]]
    tags: list[dict[str, Any]]
    payment_methods: list[dict[str, Any]]
    monthly_chart: dict[str, Any]
    net_chart: dict[str, Any]
    pace_chart: dict[str, Any] | None
    has_data: bool
    insights: list[Insight] = field(default_factory=list)
    month_comparison: MonthComparison | None = None
    is_new_user: bool = False  # nenhuma receita/despesa ainda: tela de primeiros passos
    month: datetime.date | None = None  # período de um mês: navegação ◂ ▸
    previous_month: datetime.date | None = None
    next_month: datetime.date | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def findings(self) -> list[Insight]:
        return [i for i in self.insights if not i.insufficient]

    @property
    def pending_insights(self) -> list[Insight]:
        """Análises que ainda não têm dados suficientes (declaradas, não omitidas)."""
        return [i for i in self.insights if i.insufficient]


def _list_link(**kwargs: Any) -> str:
    """URL da lista de movimentações com exatamente o recorte do indicador (drill-down)."""
    return f"{reverse('ledger:transaction_list')}?{TransactionFilter(**kwargs).to_query()}"


def _bar_rows(
    items: list[selectors.Breakdown],
    total: int,
    link: Any,
    previous: list[selectors.Breakdown] | None = None,
) -> list[dict[str, Any]]:
    """Linhas das listas de barras; com `previous`, cada linha traz a variação contra o período
    anterior equivalente (mesmo recorte pro-rata dos indicadores)."""
    top = max((i.cents for i in items), default=0)
    before = {i.key: i.cents for i in previous} if previous is not None else None
    return [
        {
            "label": i.label,
            "cents": i.cents,
            "count": i.count,
            "pct": percent(i.cents, total),
            "width": float(percent(i.cents, top) or 0),
            "color": i.color,
            "icon": i.icon,
            "link": link(i),
            "delta": Delta(i.cents, before.get(i.key, 0), up_is_good=False)
            if before is not None
            else None,
        }
        for i in items
    ]


COMPARISON_MONTHS = 6


@dataclass(frozen=True)
class ComparisonCell:
    cents: int
    link: str


@dataclass(frozen=True)
class ComparisonRow:
    label: str
    cells: list[ComparisonCell]
    average_cents: int | None  # média da base (meses fechados completos; mesmos dias se parcial)
    delta: Delta | None  # último mês vs média
    spark: list[int]  # meses fechados completos (o mês em andamento distorceria a linha)
    link: str = ""
    color: str = ""
    icon: str = ""


@dataclass(frozen=True)
class MonthComparison:
    """Despesas por categoria nos últimos meses, com a média e a variação do último mês."""

    months: list[str]
    partial_day: int | None  # último mês em andamento: comparado só até este dia
    baseline_months: int
    rows: list[ComparisonRow]
    total: ComparisonRow


def build_month_comparison(
    user: AbstractBaseUser | AnonymousUser,
    effective_end: datetime.date,
    first_activity: datetime.date | None,
    *,
    months_count: int = COMPARISON_MONTHS,
) -> MonthComparison | None:
    last = month_start(effective_end)
    first = add_months(last, -(months_count - 1))
    months = months_between(first, last)
    partial = effective_end < month_end(last)
    full = selectors.monthly_category_expenses(user, first, effective_end)
    if first_activity is None or not full:
        return None
    # Base: meses anteriores ao último, fechados e completos (desde o 1º mês inteiro de uso).
    complete_from = first_full_month(first_activity)
    baseline = [m for m in months[:-1] if m >= complete_from]
    base_source = (
        selectors.monthly_category_expenses(
            user, first, month_end(add_months(last, -1)), until_day_of_month=effective_end.day
        )
        if partial
        else full
    )
    spark_months = baseline if partial else [m for m in months if m >= complete_from]
    categories = selectors.category_refs(user)
    month_ends = [min(month_end(m), effective_end) for m in months]

    def link_for(*category_ids: uuid.UUID) -> Callable[[datetime.date, datetime.date], str]:
        def link(start: datetime.date, end: datetime.date) -> str:
            return _list_link(
                date_from=start,
                date_to=end,
                kinds=(TransactionKind.EXPENSE,),
                category_ids=category_ids,
            )

        return link

    def row(values: list[int], base_values: list[int], *category_ids: uuid.UUID) -> dict[str, Any]:
        average = round(sum(base_values) / len(base_values)) if base_values else None
        link = link_for(*category_ids)
        return {
            "cells": [
                ComparisonCell(v, link(m, e))
                for v, m, e in zip(values, months, month_ends, strict=True)
            ],
            "average_cents": average,
            "delta": Delta(values[-1], average, up_is_good=False) if average is not None else None,
            "spark": [v for v, m in zip(values, months, strict=True) if m in spark_months],
            "link": link(first, effective_end),
        }

    rows = []
    for cid in {cid for by_category in full.values() for cid in by_category}:
        category = categories.get(cid)
        data = row(
            [full.get(m, {}).get(cid, 0) for m in months],
            [base_source.get(m, {}).get(cid, 0) for m in baseline],
            cid,
        )
        rows.append(
            ComparisonRow(
                label=category.name if category else "Categoria removida",
                color=category.color if category else "",
                icon=category.icon if category else "",
                **data,
            )
        )
    rows.sort(key=lambda r: (-sum(c.cents for c in r.cells), r.label))
    total = ComparisonRow(
        label="Total",
        **row(
            [sum(full.get(m, {}).values()) for m in months],
            [sum(base_source.get(m, {}).values()) for m in baseline],
        ),
    )
    return MonthComparison(
        months=[month_label(m) for m in months],
        partial_day=effective_end.day if partial else None,
        baseline_months=len(baseline),
        rows=rows,
        total=total,
    )


def _last_closed_month(span_end: datetime.date, today: datetime.date) -> datetime.date:
    if span_end == month_end(span_end) and span_end < today:
        return month_start(span_end)
    return add_months(month_start(span_end), -1)


def build_insights(
    user: AbstractBaseUser | AnonymousUser,
    comparison: Comparison,
    *,
    single_month: bool,
    today: datetime.date,
    current: Totals,
    previous: Totals | None,
    expenses_by_category: dict[uuid.UUID, int],
    settings: InsightSettings | None = None,
    budgets: Sequence[BudgetSignal] = (),
) -> list[Insight]:
    """Agrega o histórico necessário e avalia as regras de `domain.insights`."""
    first = selectors.first_activity_date(user)
    if first is None:
        return []
    settings = settings or InsightSettings()
    span = comparison.current
    months: list[MonthData] = []
    last_closed = _last_closed_month(span.end, today)
    first_month = max(first_full_month(first), add_months(last_closed, -(HISTORY_MONTHS - 1)))
    if first_month <= last_closed:
        last_day = month_end(last_closed)
        full = selectors.monthly_category_expenses(user, first_month, last_day)
        comparable = (
            selectors.monthly_category_expenses(
                user, first_month, last_day, until_day_of_month=span.end.day
            )
            if comparison.partial and single_month
            else full
        )
        months = [
            MonthData(
                p.month,
                p.income_cents,
                p.expense_cents,
                full.get(p.month, {}),
                comparable.get(p.month, {}),
            )
            for p in selectors.monthly_series(user, first_month, last_day)
        ]
    window = weekday_window(span, today, settings.weekday_weeks)
    ctx = AnalyticsContext(
        today=today,
        current=span,
        partial=comparison.partial,
        single_month=single_month,
        totals=current,
        previous=comparison.previous,
        previous_totals=previous,
        expenses_by_category=expenses_by_category,
        category_names=selectors.category_names(user),
        months=months,
        first_date=first,
        weekday_window=window,
        daily_expenses=selectors.daily_expenses(user, window.start, window.end),
        budgets=budgets,
        settings=settings,
    )
    return evaluate(ctx)


def parse_month(value: str, today: datetime.date) -> datetime.date | None:
    """ "2026-08" → 1º/ago/2026. Inválido → None; meses futuros viram o mês atual."""
    try:
        month = datetime.date.fromisoformat(f"{value}-01")
    except ValueError:
        return None
    return min(month, month_start(today))


def build_dashboard(
    user: AbstractBaseUser | AnonymousUser,
    period: str,
    today: datetime.date,
    month: datetime.date | None = None,
    budget_signals: Callable[[datetime.date], Sequence[BudgetSignal]] | None = None,
) -> Dashboard:
    """`month` (1º dia) escolhe um mês específico, navegável com ◂ ▸; sem ele vale `period`."""
    current_month = month_start(today)
    if month is not None:
        month = min(month_start(month), current_month)
        if month == current_month:
            period = "mes"
        elif month == add_months(current_month, -1):
            period = "mes-anterior"
        else:
            period = MONTH_PERIOD
        start, end = month, month_end(month)
    else:
        if period not in dict(PERIODS):
            period = DEFAULT_PERIOD
        start, end = period_bounds(period, today)
    single_month = start == month_start(start) and end == month_end(start)
    comparison = comparable_ranges(start, end, today)
    effective_end = comparison.current.end if comparison else end
    current = selectors.totals(user, start, effective_end)
    previous = (
        selectors.totals(user, comparison.previous.start, comparison.previous.end)
        if comparison
        else None
    )

    def delta(attr: str, *, up_is_good: bool) -> Delta | None:
        if previous is None:
            return None
        return Delta(getattr(current, attr), getattr(previous, attr), up_is_good)

    elapsed_days = comparison.current.days if comparison else 1
    kpis = [
        Kpi("income", "Receitas", current.income_cents, delta("income_cents", up_is_good=True)),
        Kpi("expense", "Despesas", current.expense_cents, delta("expense_cents", up_is_good=False)),
        Kpi(
            "net",
            "Resultado",
            current.net_cents,
            delta("net_cents", up_is_good=True),
            hint="Receitas - despesas",
        ),
    ]

    # Média mensal de gastos: só meses FECHADOS (o mês corrente distorceria a média).
    closed = selectors.monthly_series(
        user, add_months(month_start(today), -3), month_start(today) - datetime.timedelta(days=1)
    )
    closed_with_data = [m for m in closed if m.income_cents or m.expense_cents]

    # Séries mensais: 6 meses (12 nos períodos longos) terminando no fim do período.
    span = 12 if period in {"12m", "ano"} else 6
    last_month = month_start(min(end, month_end(today)))
    series = selectors.monthly_series(
        user, add_months(last_month, -(span - 1)), month_end(last_month)
    )
    labels = [month_label(p.month) for p in series]
    month_links = [
        _list_link(date_from=p.month, date_to=month_end(p.month), kinds=()) for p in series
    ]
    monthly_chart = {
        "labels": labels,
        "links": month_links,
        "series": [
            {"key": "income", "label": "Receitas", "values": [p.income_cents for p in series]},
            {"key": "expense", "label": "Despesas", "values": [p.expense_cents for p in series]},
        ],
    }
    net_chart = {
        "labels": labels,
        "links": month_links,
        "values": [p.net_cents for p in series],
    }

    # Ritmo de gastos: só para períodos de um mês.
    pace_chart = None
    if single_month:
        prev_month = add_months(start, -1)
        days_in_month = (end - start).days + 1
        cur_values = cumulative(
            selectors.daily_expenses(user, start, effective_end), start, effective_end
        )
        prev_values = cumulative(
            selectors.daily_expenses(user, prev_month, month_end(prev_month)),
            prev_month,
            month_end(prev_month),
        )
        pace_chart = {
            "labels": [str(d) for d in range(1, max(days_in_month, len(prev_values)) + 1)],
            "series": [
                {
                    "key": "previous",
                    "label": f"{month_label(prev_month, with_year=False).capitalize()} (anterior)",
                    "values": prev_values,
                },
                {
                    "key": "current",
                    "label": month_label(start, with_year=False).capitalize(),
                    "values": cur_values,
                },
            ],
        }

    expense_total = current.expense_cents
    by_category = selectors.expenses_by_category(user, start, effective_end)
    categories = _bar_rows(
        by_category,
        expense_total,
        lambda i: _list_link(
            date_from=start,
            date_to=effective_end,
            kinds=(TransactionKind.EXPENSE,),
            category_ids=(i.id,),
        ),
    )
    prev_range = comparison.previous if comparison else None
    tags = _bar_rows(
        selectors.expenses_by_tag(user, start, effective_end),
        expense_total,
        lambda i: _list_link(
            date_from=start,
            date_to=effective_end,
            kinds=(TransactionKind.EXPENSE,),
            tag_ids=(i.id,),
        ),
        selectors.expenses_by_tag(user, prev_range.start, prev_range.end, limit=None)
        if prev_range
        else None,
    )
    methods = _bar_rows(
        selectors.expenses_by_payment_method(user, start, effective_end),
        expense_total,
        lambda i: _list_link(
            date_from=start,
            date_to=effective_end,
            kinds=(TransactionKind.EXPENSE,),
            payment_methods=(i.key,) if i.key else (),
        ),
        selectors.expenses_by_payment_method(user, prev_range.start, prev_range.end)
        if prev_range
        else None,
    )
    first_activity = selectors.first_activity_date(user)
    month_comparison = build_month_comparison(user, effective_end, first_activity)

    insights = (
        build_insights(
            user,
            comparison,
            single_month=single_month,
            today=today,
            current=current,
            previous=previous,
            expenses_by_category={b.id: b.cents for b in by_category if b.id is not None},
            budgets=budget_signals(start) if single_month and budget_signals else (),
        )
        if comparison
        else []
    )

    return Dashboard(
        period=period,
        start=start,
        end=end,
        comparison=comparison,
        totals=current,
        kpis=kpis,
        savings_rate=current.savings_rate,
        daily_average_cents=daily_average(current.expense_cents, elapsed_days),
        monthly_average_cents=monthly_average([m.expense_cents for m in closed_with_data]),
        closed_months=len(closed_with_data),
        categories=categories,
        tags=tags,
        payment_methods=methods,
        monthly_chart=monthly_chart,
        net_chart=net_chart,
        pace_chart=pace_chart,
        has_data=bool(current.income_cents or current.expense_cents),
        insights=insights,
        month_comparison=month_comparison,
        is_new_user=first_activity is None,
        month=start if single_month else None,
        previous_month=add_months(start, -1) if single_month else None,
        next_month=add_months(start, 1) if single_month and start < current_month else None,
    )
