import datetime
import uuid
from dataclasses import dataclass

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.db.models import BigIntegerField, Case, Count, F, Q, QuerySet, Sum, Value, When
from django.utils import timezone

from apps.taxonomy.models import Category, Tag

from .filters import TransactionFilter
from .models import Account, Transaction, TransactionKind


def list_accounts(
    user: AbstractBaseUser | AnonymousUser, *, include_archived: bool = False
) -> QuerySet[Account]:
    qs = Account.objects.for_user(user)
    if not include_archived:
        qs = qs.filter(is_archived=False)
    return qs


def list_transactions(user: AbstractBaseUser | AnonymousUser) -> QuerySet[Transaction]:
    return (
        Transaction.objects.for_user(user)
        .select_related("account", "destination_account", "category")
        .prefetch_related("tags")
    )


def account_balances(
    user: AbstractBaseUser | AnonymousUser, *, on: datetime.date | None = None
) -> dict[uuid.UUID, int]:
    """Saldo de cada conta (centavos) ao fim do dia `on` (padrão: hoje).

    `opening_balance_cents` é o saldo no início de `opening_date`; só movimentações a partir
    dessa data alteram o saldo. Lançamentos anteriores ficam como histórico para as análises.
    Cartão de crédito: saldo negativo = valor devido.
    """
    on = on or timezone.localdate()
    balances = {
        pk: opening
        for pk, opening in Account.objects.for_user(user).values_list("pk", "opening_balance_cents")
    }
    txs = Transaction.objects.for_user(user).filter(date__lte=on).order_by()

    outgoing_or_own = (
        txs.filter(date__gte=F("account__opening_date"))
        .values("account_id")
        .annotate(
            delta=Sum(
                Case(
                    When(kind=TransactionKind.INCOME, then=F("amount_cents")),
                    default=F("amount_cents") * Value(-1),
                    output_field=BigIntegerField(),
                )
            )
        )
    )
    incoming_transfers = (
        txs.filter(
            kind=TransactionKind.TRANSFER,
            date__gte=F("destination_account__opening_date"),
        )
        .values("destination_account_id")
        .annotate(delta=Sum("amount_cents"))
    )
    for own in outgoing_or_own:
        balances[own["account_id"]] += own["delta"]
    for incoming in incoming_transfers:
        balances[incoming["destination_account_id"]] += incoming["delta"]
    return balances


@dataclass(frozen=True)
class AccountBalance:
    account: Account
    balance_cents: int


def accounts_with_balances(
    user: AbstractBaseUser | AnonymousUser, *, on: datetime.date | None = None
) -> list[AccountBalance]:
    """Contas ativas com o saldo de cada uma."""
    balances = account_balances(user, on=on)
    return [AccountBalance(a, balances[a.pk]) for a in list_accounts(user)]


# Atalhos do registro rápido ----------------------------------------------------

USAGE_WINDOW_DAYS = 90


def _usage_since() -> datetime.date:
    return timezone.localdate() - datetime.timedelta(days=USAGE_WINDOW_DAYS)


def categories_by_usage(user: AbstractBaseUser | AnonymousUser, kind: str) -> QuerySet[Category]:
    """Categorias ativas do tipo, as mais usadas nos últimos 90 dias primeiro."""
    return (
        Category.objects.for_user(user)
        .filter(kind=kind, is_archived=False)
        .annotate(uses=Count("transactions", filter=Q(transactions__date__gte=_usage_since())))
        .order_by("-uses", "name")
    )


def frequent_tags(user: AbstractBaseUser | AnonymousUser, *, limit: int = 8) -> list[Tag]:
    """Tags mais usadas nos últimos 90 dias (sugestões clicáveis no formulário)."""
    return list(
        Tag.objects.for_user(user)
        .annotate(uses=Count("transactions", filter=Q(transactions__date__gte=_usage_since())))
        .filter(uses__gt=0)
        .order_by("-uses", "normalized_name")[:limit]
    )


def last_used_account(user: AbstractBaseUser | AnonymousUser) -> Account | None:
    """Conta da movimentação registrada mais recentemente (padrão do formulário)."""
    tx = (
        Transaction.objects.for_user(user)
        .filter(account__is_archived=False)
        .select_related("account")
        .order_by("-created_at")
        .first()
    )
    return tx.account if tx else list_accounts(user).first()


# Lista filtrada -------------------------------------------------------------------


def filter_transactions(
    user: AbstractBaseUser | AnonymousUser, f: TransactionFilter
) -> QuerySet[Transaction]:
    qs = list_transactions(user)
    if f.date_from:
        qs = qs.filter(date__gte=f.date_from)
    if f.date_to:
        qs = qs.filter(date__lte=f.date_to)
    if f.kinds:
        qs = qs.filter(kind__in=f.kinds)
    if f.category_ids:
        qs = qs.filter(category_id__in=f.category_ids)
    if f.tag_ids:
        # Subquery em vez de JOIN: evita linhas duplicadas e mantém as somas corretas.
        tagged = Transaction.objects.filter(tags__in=f.tag_ids).values("pk")
        qs = qs.filter(pk__in=tagged)
    if f.account_ids:
        qs = qs.filter(
            Q(account_id__in=f.account_ids) | Q(destination_account_id__in=f.account_ids)
        )
    if f.payment_methods:
        qs = qs.filter(payment_method__in=f.payment_methods)
    if f.search:
        qs = qs.filter(Q(description__icontains=f.search) | Q(notes__icontains=f.search))
    return qs


@dataclass(frozen=True)
class Totals:
    income_cents: int
    expense_cents: int
    count: int

    @property
    def net_cents(self) -> int:
        """Resultado: receitas - despesas. Transferências não entram."""
        return self.income_cents - self.expense_cents


def transaction_totals(qs: QuerySet[Transaction]) -> Totals:
    result = qs.order_by().aggregate(
        income=Sum("amount_cents", filter=Q(kind=TransactionKind.INCOME)),
        expense=Sum("amount_cents", filter=Q(kind=TransactionKind.EXPENSE)),
        count=Count("pk"),
    )
    return Totals(result["income"] or 0, result["expense"] or 0, result["count"])
