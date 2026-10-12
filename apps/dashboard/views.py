import datetime
from typing import Any

from django.http import HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from apps.analytics.dashboard import PERIODS, build_dashboard, parse_month
from apps.analytics.domain.insights import BudgetSignal, PriceSignal
from apps.analytics.domain.periods import month_end
from apps.core.http import AuthenticatedHttpRequest
from apps.goals.models import Goal
from apps.goals.selectors import goal_rows
from apps.ledger.selectors import accounts_with_balances, planned_transactions, recent_posted
from apps.planning.models import Budget, Recurrence
from apps.planning.selectors import budget_rows, month_outlook, projection_data
from apps.planning.services import materialize
from apps.shopping.selectors import price_alerts


def index(request: AuthenticatedHttpRequest) -> HttpResponse:
    today = timezone.localdate()
    materialize(request.user, today=today)  # recorrências: previstas em dia antes de mostrar
    month = parse_month(request.GET.get("mes", ""), today)

    def budget_signals(month_start: datetime.date) -> list[BudgetSignal]:
        return [
            BudgetSignal(
                label=row.budget.label,
                category_id=row.budget.category_id,
                limit_cents=row.progress.limit_cents,
                spent_cents=row.progress.spent_cents,
                expected_cents=row.progress.expected_cents,
                projected_cents=row.progress.projected_cents,
                status=row.progress.status,
                method=row.progress.method,
            )
            for row in budget_rows(request.user, month_start, today)
        ]

    prices = [
        PriceSignal(
            item=a.item.name,
            url=reverse("shopping:item_detail", args=[a.item.pk]),
            store=a.best.store,
            current_cents=a.best.total_cents,
            kind="target" if a.kind == "target" else "low",
            target_cents=a.item.target_cents,
            previous_low_cents=a.previous_low_cents,
            window_days=a.window_days,
        )
        for a in price_alerts(request.user, today)
    ]
    dashboard = build_dashboard(
        request.user,
        request.GET.get("periodo", ""),
        today,
        month,
        budget_signals,
        price_signals=prices,
    )
    accounts = accounts_with_balances(request.user)
    return render(
        request,
        "dashboard/index.html",
        {
            "dash": dashboard,
            "periods": PERIODS,
            "accounts": accounts,
            "total_balance": sum(row.balance_cents for row in accounts),
            "recent_transactions": recent_posted(request.user)[:6],
            "due_planned": list(planned_transactions(request.user).filter(date__lte=today)),
            "planning": planning_summary(request.user, today),
            "price_signals": prices,  # a tela de primeiros passos também mostra
        },
    )


def planning_summary(user: Any, today: datetime.date) -> dict[str, Any] | None:
    """Resumo do planejamento para a tela inicial (None para quem ainda não planeja nada)."""
    uses_planning = (
        Recurrence.objects.for_user(user).exists()
        or Budget.objects.for_user(user).exists()
        or Goal.objects.for_user(user).exists()
        or planned_transactions(user).exists()
    )
    if not uses_planning:
        return None
    data = projection_data(user, today)
    proj = data.projection
    month_last = min(month_end(today), proj.days[-1])
    in_accounts = [a for a in data.accounts if not a.is_liability]
    negatives = []
    for account in in_accounts:
        first = proj.first_negative(account.pk)
        if first is not None:
            negatives.append({"account": account, "first": first})
    week = today + datetime.timedelta(days=7)
    budgets = [r for r in budget_rows(user, today, today) if r.progress.status != "ok"]
    return {
        "outlook": month_outlook(user, today),
        "balance_today": sum(proj.balance_on(a.pk, today) for a in in_accounts),
        "balance_month_end": sum(proj.balance_on(a.pk, month_last) for a in in_accounts),
        "month_last": month_last,
        "negatives": negatives,
        "upcoming": list(planned_transactions(user).filter(date__gt=today, date__lte=week)[:5]),
        "tight_budgets": budgets,
        "goals": goal_rows(user, today),
    }
