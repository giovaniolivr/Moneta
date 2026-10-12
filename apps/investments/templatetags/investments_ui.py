from decimal import Decimal
from typing import Any

from django import template

from apps.analytics.domain.metrics import format_pct
from apps.core.money import format_brl

register = template.Library()


@register.filter
def percent_from_fraction(value: Decimal | None) -> str:
    """Decimal("0.1365") → "13,7%"."""
    if value is None:
        return "—"
    return format_pct((value * 100).quantize(Decimal("0.1")))


@register.filter
def split_words(value: str) -> list[str]:
    return value.split()


@register.filter
def field_named(form: Any, name: str) -> Any:
    return form[name]


@register.filter
def subtract(a: int, b: int) -> int:
    return a - b


@register.filter
def percent_from_fraction_2(value: Decimal | None) -> str:
    """Decimal("0.0292") → "2,92%"."""
    if value is None:
        return "—"
    return format_pct((value * 100).quantize(Decimal("0.01")))


@register.filter
def budget_abs_brl(cents: int) -> str:
    return format_brl(abs(cents))
