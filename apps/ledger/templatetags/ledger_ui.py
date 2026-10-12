from django import template

from apps.core.money import format_brl
from apps.ledger.models import Account, AccountKind, Transaction, TransactionKind

register = template.Library()

_ACCOUNT_ICONS = {
    AccountKind.CHECKING: "landmark",
    AccountKind.SAVINGS: "piggy-bank",
    AccountKind.CASH: "banknote",
    AccountKind.CREDIT_CARD: "credit-card",
    AccountKind.INVESTMENT: "trending-up",
}


@register.filter
def account_icon(account: Account) -> str:
    return _ACCOUNT_ICONS.get(AccountKind(account.kind), "wallet")


@register.filter
def tx_amount(tx: Transaction) -> str:
    """Valor com sinal explícito: +R$ receita, -R$ despesa, sem sinal em transferência."""
    if tx.kind == TransactionKind.INCOME:
        return format_brl(tx.amount_cents, signed=True)
    if tx.kind == TransactionKind.EXPENSE:
        return format_brl(-tx.amount_cents)
    return format_brl(tx.amount_cents)


@register.filter
def tx_amount_class(tx: Transaction) -> str:
    return {
        TransactionKind.INCOME: "amount-income",
        TransactionKind.EXPENSE: "amount-expense",
    }.get(TransactionKind(tx.kind), "amount-neutral")
