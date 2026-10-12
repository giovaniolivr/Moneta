from decimal import Decimal

from django import template

from apps.analytics.domain.metrics import format_pct

register = template.Library()


@register.filter
def percent_from_fraction(value: Decimal | None) -> str:
    """Decimal("0.1365") → "13,7%"."""
    if value is None:
        return "—"
    return format_pct((value * 100).quantize(Decimal("0.1")))
