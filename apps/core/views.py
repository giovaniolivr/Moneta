from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from apps.ledger.selectors import accounts_with_balances, list_transactions


def home(request: HttpRequest) -> HttpResponse:
    accounts = accounts_with_balances(request.user)
    return render(
        request,
        "core/home.html",
        {
            "accounts": accounts,
            "total_balance": sum(row.balance_cents for row in accounts),
            "recent_transactions": list_transactions(request.user)[:5],
        },
    )
