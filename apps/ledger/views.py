import uuid
from dataclasses import dataclass
from typing import Any

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

from apps.core.http import AuthenticatedHttpRequest
from apps.core.money import format_brl
from apps.taxonomy.models import Category, CategoryKind

from .forms import TransactionFilterForm, TransactionForm
from .models import PaymentMethod, TransactionKind
from .selectors import (
    categories_by_usage,
    filter_transactions,
    frequent_tags,
    last_used_account,
    list_transactions,
    transaction_totals,
)
from .services import create_transaction, delete_transaction, update_transaction

# Requisições do painel lateral (static/js/drawer.js) recebem só o fragmento do formulário.
FRAGMENT_HEADER = "X-Moneta-Fragment"
SAVED_EVENT_HEADER = "X-Moneta-Event"
PAGE_SIZE = 50


def _is_fragment(request: HttpRequest) -> bool:
    return request.headers.get(FRAGMENT_HEADER) == "1"


def _saved(request: HttpRequest, message: str, fallback: str) -> HttpResponse:
    """Resposta após salvar/excluir: 204 para o painel (que recarrega a página) ou redirect."""
    messages.success(request, message)
    if _is_fragment(request):
        response = HttpResponse(status=204)
        response[SAVED_EVENT_HEADER] = "transaction-saved"
        return response
    next_url = request.POST.get("next") or request.GET.get("next") or ""
    if url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return redirect(next_url)
    return redirect(fallback)


# Formulário (criar / editar) ----------------------------------------------------


def _category_groups(
    request: AuthenticatedHttpRequest, form: TransactionForm
) -> list[tuple[str, list[Category]]]:
    groups: dict[str, list[Category]] = {
        kind: list(categories_by_usage(request.user, kind))
        for kind in (CategoryKind.EXPENSE, CategoryKind.INCOME)
    }
    # Categoria arquivada de uma movimentação em edição continua selecionável.
    current = form.instance.category if form.instance else None
    if current is not None and current not in groups[current.kind]:
        groups[current.kind].append(current)
    return list(groups.items())


def _render_form(
    request: AuthenticatedHttpRequest, form: TransactionForm, *, saved_message: str = ""
) -> HttpResponse:
    tx = form.instance
    context: dict[str, Any] = {
        "form": form,
        "transaction": tx,
        "form_action": (
            reverse("ledger:transaction_update", args=[tx.pk])
            if tx
            else reverse("ledger:transaction_create")
        ),
        "form_title": "Editar movimentação" if tx else "Nova movimentação",
        "kind": form["kind"].value() or TransactionKind.EXPENSE,
        "kinds": TransactionKind.choices,
        "category_groups": _category_groups(request, form),
        "selected_category": str(form["category"].value() or ""),
        "frequent_tags": frequent_tags(request.user),
        "saved_message": saved_message,
        "next": request.POST.get("next") or request.GET.get("next") or "",
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
            return _saved(request, message, fallback="home")
    return _render_form(request, form)


@require_http_methods(["GET", "POST"])
def transaction_update(request: AuthenticatedHttpRequest, pk: uuid.UUID) -> HttpResponse:
    tx = get_object_or_404(list_transactions(request.user), pk=pk)
    if request.method == "GET":
        return _render_form(request, TransactionForm(user=request.user, instance=tx))

    form = TransactionForm(request.POST, user=request.user, instance=tx)
    if form.is_valid():
        try:
            update_transaction(tx, form.to_data())
        except ValidationError as error:
            form.add_service_errors(error)
        else:
            return _saved(request, "Movimentação atualizada.", fallback="ledger:transaction_list")
    return _render_form(request, form)


@require_POST
def transaction_delete(request: AuthenticatedHttpRequest, pk: uuid.UUID) -> HttpResponse:
    tx = get_object_or_404(list_transactions(request.user), pk=pk)
    label = f"{tx.get_kind_display()} de {format_brl(tx.amount_cents)}"
    delete_transaction(tx)
    return _saved(request, f"{label} excluída.", fallback="ledger:transaction_list")


# Lista -------------------------------------------------------------------------


@dataclass(frozen=True)
class ActiveFilter:
    label: str
    remove_url: str


def _without(params: QueryDict, key: str, value: str | None = None) -> str:
    """Query string atual sem um valor (ou sem a chave inteira) e voltando à página 1."""
    query = params.copy()
    query.pop("pagina", None)
    if value is None:
        query.pop(key, None)
    else:
        query.setlist(key, [v for v in query.getlist(key) if v != value])
    return "?" + query.urlencode()


def _active_filters(params: QueryDict, form: TransactionFilterForm) -> list[ActiveFilter]:
    data = form.cleaned_data
    chips: list[ActiveFilter] = []
    kind_labels = dict(TransactionKind.choices)
    method_labels = dict(PaymentMethod.choices)
    for kind in data.get("tipo") or ():
        chips.append(ActiveFilter(kind_labels[kind], _without(params, "tipo", kind)))
    for field, prefix in (("categoria", ""), ("tag", "#"), ("conta", "")):
        for obj in data.get(field) or ():
            chips.append(ActiveFilter(f"{prefix}{obj}", _without(params, field, str(obj.pk))))
    for method in data.get("pagamento") or ():
        chips.append(ActiveFilter(method_labels[method], _without(params, "pagamento", method)))
    if data.get("q"):
        chips.append(ActiveFilter(f"“{data['q']}”", _without(params, "q")))
    return chips


def transaction_list(request: AuthenticatedHttpRequest) -> HttpResponse:
    params = request.GET
    form = TransactionFilterForm(params, user=request.user)
    tx_filter = form.to_filter(timezone.localdate())
    transactions = filter_transactions(request.user, tx_filter)
    page = Paginator(transactions, PAGE_SIZE).get_page(params.get("pagina"))
    page_query = params.copy()
    page_query.pop("pagina", None)
    return render(
        request,
        "ledger/transaction_list.html",
        {
            "filter_form": form,
            "filter": tx_filter,
            "period": form.period(),
            "active_filters": _active_filters(params, form),
            "totals": transaction_totals(transactions),
            "page": page,
            "page_query": page_query.urlencode(),
            "has_extra_filters": any(
                params.getlist(k) for k in ("tipo", "categoria", "tag", "conta", "pagamento")
            ),
        },
    )
