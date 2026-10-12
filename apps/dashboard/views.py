from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from apps.analytics.dashboard import PERIODS, build_dashboard, parse_month
from apps.core.http import AuthenticatedHttpRequest
from apps.ledger.selectors import accounts_with_balances, planned_transactions, recent_posted
from apps.planning.services import materialize


def index(request: AuthenticatedHttpRequest) -> HttpResponse:
    today = timezone.localdate()
    materialize(request.user, today=today)  # recorrências: previstas em dia antes de mostrar
    month = parse_month(request.GET.get("mes", ""), today)
    dashboard = build_dashboard(request.user, request.GET.get("periodo", ""), today, month)
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
