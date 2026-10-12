import datetime
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.db.models import Count, Min, Q, QuerySet, Sum

from apps.analytics import selectors as analytics
from apps.analytics.domain.periods import (
    add_months,
    first_full_month,
    month_end,
    month_start,
    months_between,
)
from apps.ledger.models import TransactionStatus

from .domain.budget import BudgetProgress, progress, typical_fraction
from .models import Budget, InstallmentPlan, Recurrence

UserLike = AbstractBaseUser | AnonymousUser


def list_recurrences(user: UserLike) -> QuerySet[Recurrence]:
    return (
        Recurrence.objects.for_user(user)
        .select_related("account", "destination_account", "category")
        .prefetch_related("tags")
    )


def list_budgets(user: UserLike) -> QuerySet[Budget]:
    return Budget.objects.for_user(user).select_related("category")


def list_installment_plans(user: UserLike) -> QuerySet[InstallmentPlan]:
    """Compras parceladas com o andamento: parcelas pagas, quanto falta e a próxima data."""
    planned = Q(transactions__status=TransactionStatus.PLANNED)
    return (
        InstallmentPlan.objects.for_user(user)
        .select_related("account", "category")
        .prefetch_related("tags")
        .annotate(
            paid=Count("transactions", filter=Q(transactions__status=TransactionStatus.POSTED)),
            remaining_cents=Sum("transactions__amount_cents", filter=planned),
            next_date=Min("transactions__date", filter=planned),
        )
    )


BUDGET_BASELINE_MONTHS = 3


@dataclass(frozen=True)
class BudgetRow:
    budget: Budget
    progress: BudgetProgress


def budget_rows(user: UserLike, month: datetime.date, today: datetime.date) -> list[BudgetRow]:
    """Andamento de cada orçamento no mês (o total primeiro, depois o mais apertado).

    Mês em andamento: o ritmo esperado vem da fração que o usuário costuma gastar até o mesmo
    dia, nos até 3 meses completos anteriores (`domain.budget.typical_fraction`).
    """
    budgets = list(Budget.objects.for_user(user).select_related("category"))
    if not budgets:
        return []
    start, end = month_start(month), month_end(month)
    data_until = min(end, today)
    by_category = {b.id: b.cents for b in analytics.expenses_by_category(user, start, data_until)}
    total = analytics.totals(user, start, data_until).expense_cents

    fractions: dict[uuid.UUID | None, Decimal | None] = {}
    first = analytics.first_activity_date(user)
    if data_until < end and first is not None:
        base_first = max(first_full_month(first), add_months(start, -BUDGET_BASELINE_MONTHS))
        base_last = add_months(start, -1)
        if base_first <= base_last:
            last_day = month_end(base_last)
            full = analytics.monthly_category_expenses(user, base_first, last_day)
            until = analytics.monthly_category_expenses(
                user, base_first, last_day, until_day_of_month=data_until.day
            )
            months = months_between(base_first, base_last)

            def series(source: dict[datetime.date, dict[uuid.UUID, int]], key: Any) -> list[int]:
                if key is None:
                    return [sum(source.get(m, {}).values()) for m in months]
                return [source.get(m, {}).get(key, 0) for m in months]

            for b in budgets:
                fractions[b.category_id] = typical_fraction(
                    series(until, b.category_id), series(full, b.category_id)
                )

    rows = [
        BudgetRow(
            b,
            progress(
                limit_cents=b.amount_cents,
                spent_cents=by_category.get(b.category_id, 0) if b.category_id else total,
                month_start=start,
                month_end=end,
                data_until=data_until,
                fraction=fractions.get(b.category_id),
            ),
        )
        for b in budgets
    ]
    rows.sort(key=lambda r: (r.budget.category_id is not None, -(r.progress.used_pct or 0)))
    return rows
