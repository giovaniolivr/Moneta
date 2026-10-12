from decimal import Decimal

from django import template

register = template.Library()


@register.filter
def bar_width(pct: Decimal | None) -> str:
    """Largura da barra (0-100) — passou do limite, a barra fica cheia."""
    return f"{min(float(pct or 0), 100):.1f}"


@register.filter
def share_x(share: Decimal) -> str:
    return f"{min(float(share) * 100, 100):.1f}"


@register.filter
def budget_abs(cents: int) -> int:
    return abs(cents)
