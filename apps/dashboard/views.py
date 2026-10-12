from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from apps.analytics.dashboard import PERIODS, build_dashboard
from apps.core.http import AuthenticatedHttpRequest
from apps.ledger.selectors import accounts_with_balances, list_transactions


def index(request: AuthenticatedHttpRequest) -> HttpResponse:
    dashboard = build_dashboard(request.user, request.GET.get("periodo", ""), timezone.localdate())
    accounts = accounts_with_balances(request.user)
    return render(
        request,
        "dashboard/index.html",
        {
            "dash": dashboard,
            "periods": PERIODS,
            "accounts": accounts,
            "total_balance": sum(row.balance_cents for row in accounts),
            "recent_transactions": list_transactions(request.user)[:6],
        },
    )
