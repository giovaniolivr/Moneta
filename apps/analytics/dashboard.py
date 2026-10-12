"""Monta os dados da dashboard (indicadores, séries e detalhamentos) para um período."""

import datetime
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.urls import reverse

from apps.ledger.filters import TransactionFilter
from apps.ledger.models import TransactionKind

from . import selectors
from .domain.metrics import Delta, Totals, cumulative, daily_average, monthly_average, percent
from .domain.periods import (
    Comparison,
    add_months,
    comparable_ranges,
    month_end,
    month_start,
)

PERIODS: list[tuple[str, str]] = [
    ("mes", "Este mês"),
    ("mes-anterior", "Mês anterior"),
    ("3m", "Últimos 3 meses"),
    ("12m", "Últimos 12 meses"),
    ("ano", "Este ano"),
]
DEFAULT_PERIOD = "mes"
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
    extras: dict[str, Any] = field(default_factory=dict)


def _list_link(**kwargs: Any) -> str:
    """URL da lista de movimentações com exatamente o recorte do indicador (drill-down)."""
    return f"{reverse('ledger:transaction_list')}?{TransactionFilter(**kwargs).to_query()}"


def _bar_rows(items: list[selectors.Breakdown], total: int, link: Any) -> list[dict[str, Any]]:
    top = max((i.cents for i in items), default=0)
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
        }
        for i in items
    ]


def build_dashboard(
    user: AbstractBaseUser | AnonymousUser, period: str, today: datetime.date
) -> Dashboard:
    if period not in dict(PERIODS):
        period = DEFAULT_PERIOD
    start, end = period_bounds(period, today)
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
    if period in {"mes", "mes-anterior"}:
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
    categories = _bar_rows(
        selectors.expenses_by_category(user, start, effective_end),
        expense_total,
        lambda i: _list_link(
            date_from=start,
            date_to=effective_end,
            kinds=(TransactionKind.EXPENSE,),
            category_ids=(i.id,),
        ),
    )
    tags = _bar_rows(
        selectors.expenses_by_tag(user, start, effective_end),
        expense_total,
        lambda i: _list_link(date_from=start, date_to=effective_end, tag_ids=(i.id,)),
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
    )
