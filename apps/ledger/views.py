import uuid
from dataclasses import dataclass
from typing import Any

from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import HttpResponse, QueryDict
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from apps.core.drawer import done, render_drawer
from apps.core.forms import apply_service_errors
from apps.core.http import AuthenticatedHttpRequest
from apps.core.money import format_brl
from apps.taxonomy.models import Category, CategoryKind

from .forms import AccountForm, TransactionFilterForm, TransactionForm
from .models import Account, PaymentMethod, TransactionKind
from .selectors import (
    accounts_with_balances,
    categories_by_usage,
    filter_transactions,
    frequent_tags,
    last_used_account,
    list_accounts,
    list_transactions,
    transaction_totals,
)
from .services import (
    create_account,
    create_transaction,
    delete_account,
    delete_transaction,
    set_account_archived,
    update_account,
    update_transaction,
)

PAGE_SIZE = 50


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
    return render_drawer(
        request, "ledger/_transaction_form.html", context, title=context["form_title"]
    )


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
            return done(request, message, fallback="home")
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
            return done(request, "Movimentação atualizada.", fallback="ledger:transaction_list")
    return _render_form(request, form)


@require_POST
def transaction_delete(request: AuthenticatedHttpRequest, pk: uuid.UUID) -> HttpResponse:
    tx = get_object_or_404(list_transactions(request.user), pk=pk)
    label = f"{tx.get_kind_display()} de {format_brl(tx.amount_cents)}"
    delete_transaction(tx)
    return done(request, f"{label} excluída.", fallback="ledger:transaction_list")


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


# Contas ------------------------------------------------------------------------

ACCOUNTS = "ledger:account_list"


def account_list(request: AuthenticatedHttpRequest) -> HttpResponse:
    rows = accounts_with_balances(request.user, include_archived=True)
    active = [r for r in rows if not r.account.is_archived]
    return render(
        request,
        "ledger/account_list.html",
        {
            "accounts": active,
            "archived": [r for r in rows if r.account.is_archived],
            "assets_cents": sum(r.balance_cents for r in active if not r.account.is_liability),
            "liabilities_cents": sum(r.balance_cents for r in active if r.account.is_liability),
            "total_cents": sum(r.balance_cents for r in active),
        },
    )


def _account_drawer(
    request: AuthenticatedHttpRequest,
    form: AccountForm,
    *,
    account: Account | None = None,
    action_error: str = "",
) -> HttpResponse:
    return render_drawer(
        request,
        "ledger/_account_form.html",
        {"form": form, "account": account, "action_error": action_error},
        title="Editar conta" if account else "Nova conta",
    )


@require_http_methods(["GET", "POST"])
def account_create(request: AuthenticatedHttpRequest) -> HttpResponse:
    form = AccountForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            account = create_account(
                request.user,
                name=data["name"],
                kind=data["kind"],
                opening_balance_cents=form.opening_balance_cents(),
                opening_date=data["opening_date"],
            )
        except ValidationError as error:
            apply_service_errors(form, error, {"opening_balance_cents": "opening_balance"})
        else:
            return done(request, f"Conta “{account}” criada.", fallback=ACCOUNTS)
    return _account_drawer(request, form)


@require_http_methods(["GET", "POST"])
def account_update(request: AuthenticatedHttpRequest, pk: uuid.UUID) -> HttpResponse:
    account = get_object_or_404(list_accounts(request.user, include_archived=True), pk=pk)
    form = AccountForm(request.POST or None, instance=account)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            update_account(
                account,
                name=data["name"],
                opening_balance_cents=form.opening_balance_cents(),
                opening_date=data["opening_date"],
            )
        except ValidationError as error:
            apply_service_errors(form, error, {"opening_balance_cents": "opening_balance"})
        else:
            return done(request, f"Conta “{account}” atualizada.", fallback=ACCOUNTS)
    return _account_drawer(request, form, account=account)


def _account_action_failed(
    request: AuthenticatedHttpRequest, account: Account, error: ValidationError
) -> HttpResponse:
    return _account_drawer(
        request, AccountForm(instance=account), account=account, action_error=error.messages[0]
    )


@require_POST
def account_archive(request: AuthenticatedHttpRequest, pk: uuid.UUID) -> HttpResponse:
    account = get_object_or_404(list_accounts(request.user, include_archived=True), pk=pk)
    try:
        set_account_archived(account, archived=not account.is_archived)
    except ValidationError as error:
        return _account_action_failed(request, account, error)
    verb = "arquivada" if account.is_archived else "restaurada"
    return done(request, f"Conta “{account}” {verb}.", fallback=ACCOUNTS)


@require_POST
def account_delete(request: AuthenticatedHttpRequest, pk: uuid.UUID) -> HttpResponse:
    account = get_object_or_404(list_accounts(request.user, include_archived=True), pk=pk)
    name = str(account)
    try:
        delete_account(account)
    except ValidationError as error:
        return _account_action_failed(request, account, error)
    return done(request, f"Conta “{name}” excluída.", fallback=ACCOUNTS)
