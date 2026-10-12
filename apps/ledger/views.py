from typing import Any

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from apps.core.http import AuthenticatedHttpRequest
from apps.core.money import format_brl
from apps.taxonomy.models import CategoryKind

from .forms import TransactionForm
from .models import TransactionKind
from .selectors import categories_by_usage, frequent_tags, last_used_account
from .services import create_transaction

# Requisições do painel lateral (static/js/drawer.js) recebem só o fragmento do formulário.
FRAGMENT_HEADER = "X-Moneta-Fragment"
SAVED_EVENT_HEADER = "X-Moneta-Event"


def _is_fragment(request: HttpRequest) -> bool:
    return request.headers.get(FRAGMENT_HEADER) == "1"


def _render_form(
    request: AuthenticatedHttpRequest, form: TransactionForm, *, saved_message: str = ""
) -> HttpResponse:
    kind = form["kind"].value() or TransactionKind.EXPENSE
    context: dict[str, Any] = {
        "form": form,
        "kind": kind,
        "kinds": TransactionKind.choices,
        "category_groups": [
            (CategoryKind.EXPENSE, categories_by_usage(request.user, CategoryKind.EXPENSE)),
            (CategoryKind.INCOME, categories_by_usage(request.user, CategoryKind.INCOME)),
        ],
        "selected_category": str(form["category"].value() or ""),
        "frequent_tags": frequent_tags(request.user),
        "saved_message": saved_message,
    }
    template = (
        "ledger/_transaction_form.html" if _is_fragment(request) else "ledger/transaction_form.html"
    )
    return render(request, template, context)


@require_http_methods(["GET", "POST"])
def transaction_create(request: AuthenticatedHttpRequest) -> HttpResponse:
    user = request.user
    if request.method == "GET":
        form = TransactionForm(user=user, initial={"account": last_used_account(user)})
        return _render_form(request, form)

    form = TransactionForm(request.POST, user=user)
    if form.is_valid():
        try:
            tx = create_transaction(user, form.to_data())
        except ValidationError as error:
            form.add_service_errors(error)
        else:
            message = f"{tx.get_kind_display()} de {format_brl(tx.amount_cents)} registrada."
            if "save_and_new" in request.POST:
                # Mantém tipo, conta e data: o próximo lançamento costuma ser parecido.
                fresh = TransactionForm(
                    user=user,
                    initial={"kind": tx.kind, "account": tx.account, "date": tx.date},
                )
                return _render_form(request, fresh, saved_message=message)
            messages.success(request, message)
            if _is_fragment(request):
                response = HttpResponse(status=204)
                response[SAVED_EVENT_HEADER] = "transaction-saved"
                return response
            return redirect("home")
    return _render_form(request, form)
