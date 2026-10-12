"""Agregações para a dashboard. Todas partem de `filter_transactions` (escopo por usuário e
mesma semântica de filtro da lista) e somam no banco. Transferências nunca entram em
receitas/despesas."""

import datetime
import uuid
from dataclasses import dataclass

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.db.models import Count, Q, QuerySet, Sum
from django.db.models.functions import TruncMonth

from apps.ledger.filters import TransactionFilter
from apps.ledger.models import PaymentMethod, Transaction, TransactionKind
from apps.ledger.selectors import filter_transactions

from .domain.metrics import Totals
from .domain.periods import months_between

UserLike = AbstractBaseUser | AnonymousUser


def _in_range(
    user: UserLike, start: datetime.date | None, end: datetime.date | None
) -> QuerySet[Transaction]:
    return filter_transactions(user, TransactionFilter(date_from=start, date_to=end)).order_by()


def totals(user: UserLike, start: datetime.date | None, end: datetime.date | None) -> Totals:
    result = _in_range(user, start, end).aggregate(
        income=Sum("amount_cents", filter=Q(kind=TransactionKind.INCOME)),
        expense=Sum("amount_cents", filter=Q(kind=TransactionKind.EXPENSE)),
    )
    return Totals(result["income"] or 0, result["expense"] or 0)


@dataclass(frozen=True)
class MonthPoint:
    month: datetime.date
    income_cents: int
    expense_cents: int

    @property
    def net_cents(self) -> int:
        return self.income_cents - self.expense_cents


def monthly_series(
    user: UserLike, first_month: datetime.date, last_day: datetime.date
) -> list[MonthPoint]:
    """Receitas e despesas por mês, incluindo meses sem lançamentos (zero)."""
    rows = (
        _in_range(user, first_month, last_day)
        .annotate(month=TruncMonth("date"))
        .values("month")
        .annotate(
            income=Sum("amount_cents", filter=Q(kind=TransactionKind.INCOME)),
            expense=Sum("amount_cents", filter=Q(kind=TransactionKind.EXPENSE)),
        )
    )
    by_month = {r["month"]: (r["income"] or 0, r["expense"] or 0) for r in rows}
    return [MonthPoint(m, *by_month.get(m, (0, 0))) for m in months_between(first_month, last_day)]


@dataclass(frozen=True)
class Breakdown:
    """Uma fatia de um detalhamento (categoria, tag, forma de pagamento)."""

    key: str
    label: str
    cents: int
    count: int
    color: str = ""
    icon: str = ""
    id: uuid.UUID | None = None


def expenses_by_category(
    user: UserLike, start: datetime.date | None, end: datetime.date | None
) -> list[Breakdown]:
    rows = (
        _in_range(user, start, end)
        .filter(kind=TransactionKind.EXPENSE)
        .values("category_id", "category__name", "category__color", "category__icon")
        .annotate(cents=Sum("amount_cents"), count=Count("pk"))
        .order_by("-cents")
    )
    return [
        Breakdown(
            key=str(r["category_id"]),
            label=r["category__name"],
            cents=r["cents"],
            count=r["count"],
            color=r["category__color"],
            icon=r["category__icon"],
            id=r["category_id"],
        )
        for r in rows
    ]


def expenses_by_tag(
    user: UserLike, start: datetime.date | None, end: datetime.date | None, *, limit: int = 8
) -> list[Breakdown]:
    """Despesas por tag. Uma movimentação com várias tags conta em cada uma delas."""
    rows = (
        _in_range(user, start, end)
        .filter(kind=TransactionKind.EXPENSE, tags__isnull=False)
        .values("tags__id", "tags__name")
        .annotate(cents=Sum("amount_cents"), count=Count("pk"))
        .order_by("-cents")[:limit]
    )
    return [
        Breakdown(
            key=str(r["tags__id"]),
            label=r["tags__name"],
            cents=r["cents"],
            count=r["count"],
            id=r["tags__id"],
        )
        for r in rows
    ]


def expenses_by_payment_method(
    user: UserLike, start: datetime.date | None, end: datetime.date | None
) -> list[Breakdown]:
    labels = dict(PaymentMethod.choices)
    rows = (
        _in_range(user, start, end)
        .filter(kind=TransactionKind.EXPENSE)
        .values("payment_method")
        .annotate(cents=Sum("amount_cents"), count=Count("pk"))
        .order_by("-cents")
    )
    return [
        Breakdown(
            key=r["payment_method"] or "",
            label=labels.get(r["payment_method"], "Não informado"),
            cents=r["cents"],
            count=r["count"],
        )
        for r in rows
    ]


def daily_expenses(
    user: UserLike, start: datetime.date, end: datetime.date
) -> dict[datetime.date, int]:
    rows = (
        _in_range(user, start, end)
        .filter(kind=TransactionKind.EXPENSE)
        .values("date")
        .annotate(cents=Sum("amount_cents"))
    )
    return {r["date"]: r["cents"] for r in rows}
