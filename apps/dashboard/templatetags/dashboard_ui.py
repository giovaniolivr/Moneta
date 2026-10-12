from decimal import Decimal
from typing import Any

from django import template

register = template.Library()


@register.filter
def pct(value: Decimal | None) -> str:
    """Decimal(18.0) → "18%"; Decimal(33.3) → "33,3%"; None → "—"."""
    if value is None:
        return "—"
    text = f"{abs(value):.1f}".replace(".", ",").removesuffix(",0")
    return f"{'-' if value < 0 else ''}{text}%"


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
