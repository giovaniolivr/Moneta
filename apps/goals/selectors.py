import datetime
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.db.models import QuerySet

from apps.analytics import selectors as analytics
from apps.analytics.domain.metrics import format_pct
from apps.analytics.domain.periods import add_months, first_full_month, month_end, month_start
from apps.investments.domain.products import MissingRateError, Taxation
from apps.investments.domain.rates import annual_to_monthly
from apps.investments.domain.taxes import ir_rate
from apps.investments.models import Series
from apps.investments.selectors import effective_rates
from apps.ledger.models import Transaction, TransactionKind, TransactionStatus
from apps.ledger.selectors import account_balances

from .domain.progress import GoalProgress, goal_progress
from .models import Goal

UserLike = AbstractBaseUser | AnonymousUser
HISTORY_MONTHS = 3


def list_goals(user: UserLike) -> QuerySet[Goal]:
    return Goal.objects.for_user(user).select_related("account")


@dataclass(frozen=True)
class GoalRow:
    goal: Goal
    progress: GoalProgress
    rate_note: str = ""  # como o rendimento estimado entrou (evidência)


def _contributions(user: UserLike, account: Any, months: list[datetime.date]) -> list[int]:
    """Aporte líquido de cada mês: transferências para a conta menos as que saem dela."""
    if not months:
        return []
    transfers = Transaction.objects.for_user(user).filter(
        status=TransactionStatus.POSTED,
        kind=TransactionKind.TRANSFER,
        date__gte=months[0],
        date__lte=month_end(months[-1]),
    )
    totals = dict.fromkeys(months, 0)
    for day, cents, source, destination in transfers.values_list(
        "date", "amount_cents", "account_id", "destination_account_id"
    ):
        key = month_start(day)
        if destination == account.pk:
            totals[key] += cents
        if source == account.pk:
            totals[key] -= cents
    return [totals[m] for m in months]


def _monthly_rate(
    goal: Goal, rates: dict[Series, Any], today: datetime.date
) -> tuple[Decimal, str]:
    """Rendimento estimado líquido ao mês da conta da meta e uma descrição (evidência).

    IR: alíquota pela idade média do dinheiro até o prazo (metade do prazo); sem prazo, 17,5%.
    """
    product = goal.account.product
    if product is None:
        return Decimal(0), ""
    try:
        daily = product.spec.daily_gross_rate(
            cdi=rates[Series.CDI].annual, selic=rates[Series.SELIC].annual
        )
    except MissingRateError as exc:
        return Decimal(0), f"sem a taxa {exc}, o rendimento não entra na conta"
    monthly = annual_to_monthly((1 + daily) ** 252 - 1)
    if product.spec.taxation == Taxation.TAXED:
        days = (goal.deadline - today).days // 2 if goal.deadline else 361
        ir = ir_rate(max(days, 1))
        monthly *= 1 - ir
        tax_note = f", líquido de IR de {format_pct(ir * 100)}"
    else:
        tax_note = ", isento de IR"
    rate_text = format_pct((monthly * 100).quantize(Decimal("0.01")))
    return monthly, f"{product.name} ({product.rate_label}): ~{rate_text} ao mês{tax_note}"


def goal_rows(user: UserLike, today: datetime.date) -> list[GoalRow]:
    """Metas com o andamento: saldo atual, aportes recentes (transferências), rendimento
    estimado do produto da conta e capacidade de poupança (resultado médio dos meses fechados)."""
    goals = list(list_goals(user).select_related("account__product"))
    if not goals:
        return []
    current_month = month_start(today)
    window = [add_months(current_month, -k) for k in range(HISTORY_MONTHS, 0, -1)]
    current = account_balances(user, on=today)
    rates = effective_rates(user)

    first = analytics.first_activity_date(user)
    results: list[int] = []
    if first is not None:
        since = max(first_full_month(first), add_months(current_month, -HISTORY_MONTHS))
        if since < current_month:
            series = analytics.monthly_series(user, since, current_month - datetime.timedelta(1))
            results = [p.net_cents for p in series]

    rows = []
    for goal in goals:
        # Antes de a conta existir no app, não há aportes a medir.
        months = [m for m in window if month_end(m) >= goal.account.opening_date]
        rate, rate_note = _monthly_rate(goal, rates, today)
        rows.append(
            GoalRow(
                goal,
                goal_progress(
                    target_cents=goal.target_cents,
                    monthly_contributions=_contributions(user, goal.account, months),
                    current_balance_cents=current.get(goal.account_id, 0),
                    monthly_results=results,
                    today=today,
                    deadline=goal.deadline,
                    monthly_rate=rate,
                ),
                rate_note,
            )
        )
    return rows
