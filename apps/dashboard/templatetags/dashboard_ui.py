from decimal import Decimal
from typing import Any

from django import template
from django.urls import reverse

from apps.analytics.domain.insights import Evidence
from apps.analytics.domain.metrics import format_pct
from apps.core.money import format_brl
from apps.ledger.filters import TransactionFilter

register = template.Library()


@register.filter
def pct(value: Decimal | None) -> str:
    """Decimal(18.0) → "18%"; Decimal(33.3) → "33,3%"; None → "—"."""
    return format_pct(value)


@register.filter
def monthly_rows(chart: dict[str, Any]) -> list[dict[str, Any]]:
    """Linhas da tabela equivalente ao gráfico mensal (mais recente primeiro)."""
    income, expense = (s["values"] for s in chart["series"])
    rows = [
        {"label": label, "link": link, "income": i, "expense": e, "net": i - e}
        for label, link, i, e in zip(chart["labels"], chart["links"], income, expense, strict=True)
    ]
    return rows[::-1]


@register.filter
def pace_rows(chart: dict[str, Any]) -> list[dict[str, Any]]:
    series = chart["series"]
    return [
        {
            "day": day,
            "values": [s["values"][i] if i < len(s["values"]) else None for s in series],
        }
        for i, day in enumerate(chart["labels"])
    ]


_SEVERITY = {
    "attention": ("circle-alert", "Atenção"),
    "positive": ("circle-check", "Ponto positivo"),
    "info": ("info", "Informação"),
}


@register.filter
def insight_icon(severity: str) -> str:
    return _SEVERITY.get(severity, _SEVERITY["info"])[0]


@register.filter
def insight_severity_label(severity: str) -> str:
    return _SEVERITY.get(severity, _SEVERITY["info"])[1]


@register.filter
def evidence_value(item: Evidence) -> str:
    if item.value is None:
        return "—"
    if item.unit == "brl" and isinstance(item.value, int):
        return format_brl(item.value)
    if item.unit == "pct" and isinstance(item.value, Decimal):
        return format_pct(item.value)
    return str(item.value)


@register.filter
def transaction_list_url(transaction_filter: TransactionFilter) -> str:
    return f"{reverse('ledger:transaction_list')}?{transaction_filter.to_query()}"


@register.filter
def spark_points(values: list[int]) -> str:
    """Pontos de uma sparkline no viewBox 0 0 100 24 (1px de folga em cima e embaixo).

    O eixo começa em zero: variações pequenas continuam parecendo pequenas."""
    if len(values) < 2:
        return ""
    low, high = min(0, *values), max(values)
    span = high - low or 1
    step = 100 / (len(values) - 1)
    return " ".join(
        f"{i * step:.1f},{23 - (v - low) / span * 22:.1f}" for i, v in enumerate(values)
    )
