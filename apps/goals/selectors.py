import datetime
from dataclasses import dataclass

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.db.models import QuerySet

from apps.analytics import selectors as analytics
from apps.analytics.domain.periods import add_months, first_full_month, month_end, month_start
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


def goal_rows(user: UserLike, today: datetime.date) -> list[GoalRow]:
    """Metas com o andamento: saldo atual, ritmo de aportes (variação do saldo nos últimos
    meses fechados) e capacidade de poupança (resultado médio dos meses fechados)."""
    goals = list(list_goals(user))
    if not goals:
        return []
    current_month = month_start(today)
    # Fim dos últimos meses fechados + o fim do mês anterior a eles (base da 1ª variação).
    ends = [month_end(add_months(current_month, -k)) for k in range(HISTORY_MONTHS + 1, 0, -1)]
    balances_on = {day: account_balances(user, on=day) for day in ends}
    current = account_balances(user, on=today)

    first = analytics.first_activity_date(user)
    results: list[int] = []
    if first is not None:
        since = max(first_full_month(first), add_months(current_month, -HISTORY_MONTHS))
        if since < current_month:
            series = analytics.monthly_series(user, since, current_month - datetime.timedelta(1))
            results = [p.net_cents for p in series]

    rows = []
    for goal in goals:
        opened = goal.account.opening_date
        # Antes de a conta existir no app, a variação do saldo não diz nada.
        usable = [day for day in ends if day >= opened - datetime.timedelta(days=1)]
        rows.append(
            GoalRow(
                goal,
                goal_progress(
                    target_cents=goal.target_cents,
                    month_end_balances=[balances_on[d].get(goal.account_id, 0) for d in usable],
                    current_balance_cents=current.get(goal.account_id, 0),
                    monthly_results=results,
                    today=today,
                    deadline=goal.deadline,
                ),
            )
        )
    return rows
