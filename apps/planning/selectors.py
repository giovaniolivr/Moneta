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
from apps.ledger.models import Account, Transaction, TransactionKind, TransactionStatus
from apps.ledger.selectors import account_balances

from .domain.budget import BudgetProgress, progress, typical_fraction
from .domain.projection import Billing, Event, PendingPayment, Projection, project
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


# Projeção de saldo -------------------------------------------------------------


@dataclass(frozen=True)
class ProjectionData:
    projection: Projection
    accounts: list[Account]  # na ordem de exibição (cartões por último)
    unconfigured_cards: list[Account]  # cartões sem fechamento/vencimento/pagadora
    variable: VariableEstimate  # gasto variável incluído (vazio se desligado)


def _planned_events(user: UserLike, end: datetime.date) -> list[Event]:
    events: list[Event] = []
    planned = Transaction.objects.for_user(user).filter(
        status=TransactionStatus.PLANNED, date__lte=end
    )
    for tx in planned.only("kind", "amount_cents", "date", "account", "destination_account"):
        if tx.kind == TransactionKind.INCOME:
            events.append(Event(tx.date, tx.account_id, tx.amount_cents))
        elif tx.kind == TransactionKind.EXPENSE:
            events.append(Event(tx.date, tx.account_id, -tx.amount_cents))
        elif tx.destination_account_id is not None:
            events.append(Event(tx.date, tx.account_id, -tx.amount_cents))
            events.append(Event(tx.date, tx.destination_account_id, tx.amount_cents))
    return events


def _pending_payment(
    user: UserLike, billing: Billing, today: datetime.date
) -> PendingPayment | None:
    """Fatura já fechada cujo vencimento ainda não passou: quanto falta pagar."""
    closing = billing.last_closing_before(today)
    due = billing.due_for(closing)
    if due < today:
        return None
    owed = max(0, -account_balances(user, on=closing).get(billing.card_id, 0))
    paid = (
        Transaction.objects.for_user(user)
        .filter(
            status=TransactionStatus.POSTED,
            kind=TransactionKind.TRANSFER,
            destination_account_id=billing.card_id,
            date__gt=closing,
            date__lte=today,
        )
        .aggregate(total=Sum("amount_cents"))["total"]
        or 0
    )
    return PendingPayment(billing.card_id, closing, due, max(0, owed - paid))


def projection_data(
    user: UserLike, today: datetime.date, *, days: int = 92, include_variable: bool = True
) -> ProjectionData:
    """Com `include_variable`, soma o gasto variável médio (dia a dia, a partir de amanhã)."""
    end = today + datetime.timedelta(days=days)
    opening = account_balances(user, on=today)
    accounts = [
        a
        for a in Account.objects.for_user(user).select_related("payment_account")
        if not a.is_archived or opening.get(a.pk, 0) != 0
    ]
    accounts.sort(key=lambda a: (a.is_liability, a.name.lower()))
    billings = [
        Billing(a.pk, a.payment_account_id, a.statement_closing_day, a.payment_due_day)  # type: ignore[arg-type]
        for a in accounts
        if a.has_billing
    ]
    pending = [p for b in billings if (p := _pending_payment(user, b, today)) is not None]
    events = _planned_events(user, end)
    variable = variable_spending(user, today) if include_variable else VariableEstimate({}, 0)
    known = {a.pk for a in accounts}
    for account_id, daily in variable.daily_by_account().items():
        if account_id not in known:
            continue
        day = today + datetime.timedelta(days=1)
        while day <= end:
            events.append(Event(day, account_id, -daily))
            day += datetime.timedelta(days=1)
    projection = project(
        today=today,
        end=end,
        opening={a.pk: opening.get(a.pk, 0) for a in accounts},
        events=events,
        billings=billings,
        pending=pending,
    )
    unconfigured = [a for a in accounts if a.is_liability and not a.has_billing]
    return ProjectionData(projection, accounts, unconfigured, variable)


# Gasto variável e mês realizado vs previsto -------------------------------------

VARIABLE_MONTHS = 3


@dataclass(frozen=True)
class VariableEstimate:
    """Média mensal dos gastos que não vêm de recorrência nem de parcela (o "dia a dia")."""

    monthly_by_account: dict[uuid.UUID, int]
    months: int  # meses completos usados (0 = sem base para estimar)

    @property
    def monthly_cents(self) -> int:
        return sum(self.monthly_by_account.values())

    def daily_by_account(self) -> dict[uuid.UUID, int]:
        return {k: round(v * 12 / 365) for k, v in self.monthly_by_account.items() if v}


def variable_spending(user: UserLike, today: datetime.date) -> VariableEstimate:
    first = analytics.first_activity_date(user)
    current = month_start(today)
    if first is None:
        return VariableEstimate({}, 0)
    since = max(first_full_month(first), add_months(current, -VARIABLE_MONTHS))
    if since >= current:
        return VariableEstimate({}, 0)
    months = len(months_between(since, add_months(current, -1)))
    rows = (
        Transaction.objects.for_user(user)
        .filter(
            status=TransactionStatus.POSTED,
            kind=TransactionKind.EXPENSE,
            recurrence__isnull=True,
            installment_plan__isnull=True,
            date__gte=since,
            date__lt=current,
        )
        .values("account_id")
        .annotate(total=Sum("amount_cents"))
        .order_by()
    )
    monthly = {r["account_id"]: r["total"] / months for r in rows}
    # Recorrência criada depois de o usuário já lançar a despesa à mão (ex.: aluguel): esse
    # histórico não está ligado à recorrência e entraria aqui E nas previstas. Em cada mês, a
    # despesa não ligada mais parecida (mesma conta e categoria, valor ±20%) sai da média.
    unlinked = Transaction.objects.for_user(user).filter(
        status=TransactionStatus.POSTED,
        kind=TransactionKind.EXPENSE,
        recurrence__isnull=True,
        installment_plan__isnull=True,
        date__gte=since,
        date__lt=current,
    )
    used: set[uuid.UUID] = set()
    expense_recurrences = Recurrence.objects.for_user(user).filter(
        is_active=True, kind=TransactionKind.EXPENSE, category__isnull=False
    )
    for rec in expense_recurrences:
        if rec.category_id is None:
            continue
        low, high = rec.amount_cents * 0.8, rec.amount_cents * 1.2
        candidates = unlinked.filter(
            account_id=rec.account_id,
            category_id=rec.category_id,
            amount_cents__gte=low,
            amount_cents__lte=high,
        ).values_list("pk", "date", "amount_cents")
        best: dict[datetime.date, tuple[uuid.UUID, int]] = {}
        for pk, day, cents in candidates:
            if pk in used:
                continue
            key = month_start(day)
            if key not in best or abs(cents - rec.amount_cents) < abs(
                best[key][1] - rec.amount_cents
            ):
                best[key] = (pk, cents)
        for pk, cents in best.values():
            used.add(pk)
            monthly[rec.account_id] = monthly.get(rec.account_id, 0) - cents / months
    return VariableEstimate({k: max(0, round(v)) for k, v in monthly.items()}, months)


@dataclass(frozen=True)
class MonthOutlook:
    """Mês em andamento: realizado até hoje + o que ainda falta (previstas e variável)."""

    income_cents: int
    expense_cents: int
    planned_income_cents: int  # previstas até o fim do mês (inclui vencidas não confirmadas)
    planned_expense_cents: int
    variable_cents: int  # gasto variável estimado para os dias que faltam
    variable: VariableEstimate

    @property
    def net_cents(self) -> int:
        return self.income_cents - self.expense_cents

    @property
    def projected_net_cents(self) -> int:
        return (
            self.net_cents
            + self.planned_income_cents
            - self.planned_expense_cents
            - self.variable_cents
        )


def month_outlook(user: UserLike, today: datetime.date) -> MonthOutlook:
    start, end = month_start(today), month_end(today)
    realized = analytics.totals(user, start, today)
    planned = Transaction.objects.for_user(user).filter(
        status=TransactionStatus.PLANNED, date__lte=end
    )
    sums = planned.aggregate(
        income=Sum("amount_cents", filter=Q(kind=TransactionKind.INCOME)),
        expense=Sum("amount_cents", filter=Q(kind=TransactionKind.EXPENSE)),
    )
    variable = variable_spending(user, today)
    days_left = (end - today).days  # a partir de amanhã: o dia de hoje já está acontecendo
    return MonthOutlook(
        income_cents=realized.income_cents,
        expense_cents=realized.expense_cents,
        planned_income_cents=sums["income"] or 0,
        planned_expense_cents=sums["expense"] or 0,
        variable_cents=round(variable.monthly_cents * 12 / 365 * days_left),
        variable=variable,
    )
