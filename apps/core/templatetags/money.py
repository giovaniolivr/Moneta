from django import template

from apps.core.money import format_brl

register = template.Library()


@register.filter
def brl(cents: int | None) -> str:
    """{{ transaction.amount_cents|brl }} → "R$ 1.234,56"."""
    return "" if cents is None else format_brl(cents)


@register.filter
def brl_signed(cents: int | None) -> str:
    """{{ result_cents|brl_signed }} → "+R$ 10,00" / "-R$ 10,00"."""
    return "" if cents is None else format_brl(cents, signed=True)
