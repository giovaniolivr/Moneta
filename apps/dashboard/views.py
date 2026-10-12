import datetime

from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from apps.analytics.dashboard import PERIODS, build_dashboard, parse_month
from apps.analytics.domain.insights import BudgetSignal
from apps.core.http import AuthenticatedHttpRequest
from apps.ledger.selectors import accounts_with_balances, planned_transactions, recent_posted
from apps.planning.selectors import budget_rows
from apps.planning.services import materialize


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

    dashboard = build_dashboard(
        request.user, request.GET.get("periodo", ""), today, month, budget_signals
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
        },
    )
