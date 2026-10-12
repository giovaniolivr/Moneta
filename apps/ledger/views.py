import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import HttpResponse, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from apps.core.drawer import done, render_drawer, safe_next
from apps.core.forms import apply_service_errors
from apps.core.http import AuthenticatedHttpRequest
from apps.core.money import format_brl
from apps.planning.services import materialize
from apps.taxonomy.models import Category, CategoryKind
from apps.taxonomy.selectors import list_categories
from apps.users.models import User

from .forms import (
    BATCH_KINDS,
    BATCH_MAX_ROWS,
    AccountForm,
    BatchHeaderForm,
    BatchRowFormSet,
    TransactionFilterForm,
    TransactionForm,
)
from .models import Account, PaymentMethod, Transaction, TransactionKind
from .selectors import (
    accounts_with_balances,
    categories_by_usage,
    filter_transactions,
    frequent_tags,
    last_used_account,
    list_accounts,
    list_transactions,
    planned_transactions,
    transaction_totals,
)
from .services import (
    BatchError,
    confirm_transaction,
    create_account,
    create_transaction,
    create_transactions,
    delete_account,
    delete_transaction,
    restore_transaction,
    set_account_archived,
    transaction_snapshot,
    update_account,
    update_transaction,
)

PAGE_SIZE = 50
UNDO_SESSION_KEY = "ledger_undo_delete"


# Formulário (criar / editar) ----------------------------------------------------


def category_groups(user: User, form: TransactionForm) -> list[tuple[str, list[Category]]]:
    """Categorias por tipo (mais usadas primeiro) para os chips do formulário."""
    groups: dict[str, list[Category]] = {
        kind: list(categories_by_usage(user, kind))
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
        "category_groups": category_groups(request.user, form),
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
    confirm = "confirm" in request.POST
    if form.is_valid() and confirm and form.cleaned_data["date"] > timezone.localdate():
        form.add_error("date", "Para confirmar, use a data de hoje ou uma anterior.")
    if form.is_valid():
        try:
            update_transaction(tx, form.to_data(confirm=confirm))
        except ValidationError as error:
            form.add_service_errors(error)
        else:
            message = "Movimentação confirmada." if confirm else "Movimentação atualizada."
            return done(request, message, fallback="ledger:transaction_list")
    return _render_form(request, form)


@require_POST
def transaction_confirm(request: AuthenticatedHttpRequest, pk: uuid.UUID) -> HttpResponse:
    """Confirma uma prevista como está (para ajustar valor/data, use a edição)."""
    tx = get_object_or_404(planned_transactions(request.user), pk=pk)
    confirm_transaction(tx, today=timezone.localdate())
    label = f"{tx.get_kind_display()} de {format_brl(tx.amount_cents)}"
    messages.success(request, f"{label} confirmada em {tx.date:%d/%m/%Y}.")
    return redirect(safe_next(request) or "ledger:planned_list")


def planned_list(request: AuthenticatedHttpRequest) -> HttpResponse:
    today = timezone.localdate()
    materialize(request.user, today=today)
    planned = list(planned_transactions(request.user))
    return render(
        request,
        "ledger/planned_list.html",
        {
            "due": [tx for tx in planned if tx.date <= today],
            "upcoming": [tx for tx in planned if tx.date > today],
            "today": today,
        },
    )


@require_POST
def transaction_delete(request: AuthenticatedHttpRequest, pk: uuid.UUID) -> HttpResponse:
    tx = get_object_or_404(list_transactions(request.user), pk=pk)
    label = f"{tx.get_kind_display()} de {format_brl(tx.amount_cents)}"
    request.session[UNDO_SESSION_KEY] = transaction_snapshot(tx)
    delete_transaction(tx)
    return done(
        request, f"{label} excluída.", fallback="ledger:transaction_list", extra_tags="undo"
    )


@require_POST
def transaction_restore(request: AuthenticatedHttpRequest) -> HttpResponse:
    """Desfaz a última exclusão de movimentação desta sessão."""
    snapshot = request.session.pop(UNDO_SESSION_KEY, None)
    if snapshot is None:
        messages.error(request, "Não há exclusão para desfazer.")
    else:
        try:
            restore_transaction(request.user, snapshot)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, "Exclusão desfeita.")
    return redirect(safe_next(request) or "ledger:transaction_list")


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
            "planned_count": planned_transactions(request.user).count(),
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


# Lançamento em lote ------------------------------------------------------------

BATCH_INITIAL_ROWS = 3


@require_http_methods(["GET", "POST"])
def transaction_batch(request: AuthenticatedHttpRequest) -> HttpResponse:
    user = request.user
    data = request.POST if request.method == "POST" else None
    header = BatchHeaderForm(
        data, user=user, prefix="lote", initial={"account": last_used_account(user)}
    )
    formset = BatchRowFormSet(
        data,
        prefix="linhas",
        form_kwargs={"user": user},
        initial=None if data else [{"kind": TransactionKind.EXPENSE}] * BATCH_INITIAL_ROWS,
    )
    batch_error = ""
    if data is not None:
        valid = header.is_valid() & formset.is_valid()  # sem curto-circuito: valida tudo
        filled = [form for form in formset.forms if not form.is_blank()]
        if not filled:
            batch_error = "Preencha ao menos uma linha."
        elif valid:
            items = [
                form.to_data(
                    default_account=header.cleaned_data["account"],
                    default_date=header.cleaned_data["date"],
                )
                for form in filled
            ]
            try:
                created = create_transactions(user, items)
            except BatchError as error:
                for index, row_error in error.errors.items():
                    apply_service_errors(filled[index], row_error, {"amount_cents": "amount"})
            else:
                return _batch_saved(request, created)
        if not batch_error and (formset.total_error_count() or header.errors):
            batch_error = "Corrija as linhas destacadas. Nada foi salvo ainda."

    categories = list(list_categories(user))
    return render(
        request,
        "ledger/transaction_batch.html",
        {
            "header": header,
            "formset": formset,
            "batch_error": batch_error,
            "is_fresh": data is None,
            "expense_categories": [c for c in categories if c.kind == CategoryKind.EXPENSE],
            "income_categories": [c for c in categories if c.kind == CategoryKind.INCOME],
            "kinds": BATCH_KINDS,
            "max_rows": BATCH_MAX_ROWS,
            "frequent_tags": frequent_tags(user, limit=30),
        },
    )


def _batch_saved(request: AuthenticatedHttpRequest, created: list[Transaction]) -> HttpResponse:
    expense = sum(t.amount_cents for t in created if t.kind == TransactionKind.EXPENSE)
    income = sum(t.amount_cents for t in created if t.kind == TransactionKind.INCOME)
    parts = [f"{len(created)} movimentaç{'ão' if len(created) == 1 else 'ões'} registrada(s)"]
    if expense:
        parts.append(f"despesas {format_brl(expense)}")
    if income:
        parts.append(f"receitas {format_brl(income)}")
    messages.success(request, " · ".join(parts) + ".")
    dates = [t.date for t in created]
    query = urlencode({"de": min(dates).isoformat(), "ate": max(dates).isoformat()})
    return redirect(f"{reverse('ledger:transaction_list')}?{query}")
