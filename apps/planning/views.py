import datetime
import uuid
from typing import Any

from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from apps.analytics.domain.periods import add_months, month_end, month_start
from apps.core.drawer import done, render_drawer
from apps.core.forms import apply_service_errors
from apps.core.http import AuthenticatedHttpRequest
from apps.goals.selectors import goal_rows
from apps.ledger.models import TransactionKind
from apps.ledger.selectors import frequent_tags, last_used_account
from apps.ledger.views import category_groups

from .forms import BudgetForm, InstallmentPlanForm, RecurrenceForm
from .models import Budget, Recurrence
from .selectors import (
    budget_rows,
    list_budgets,
    list_installment_plans,
    list_recurrences,
    projection_data,
)
from .services import (
    create_budget,
    create_recurrence,
    delete_budget,
    delete_installment_plan,
    delete_recurrence,
    materialize,
    set_recurrence_active,
    update_budget,
    update_installment_plan,
    update_recurrence,
)

INDEX = "planning:index"


def index(request: AuthenticatedHttpRequest) -> HttpResponse:
    today = timezone.localdate()
    materialize(request.user, today=today)
    recurrences = list(list_recurrences(request.user))
    active = [r for r in recurrences if r.is_active]

    def monthly(kind: str) -> int:
        return sum(r.monthly_cents for r in active if r.kind == kind)

    return render(
        request,
        "planning/index.html",
        {
            "goals": goal_rows(request.user, today),
            "budgets": budget_rows(request.user, today, today),
            "month": today,
            "plans": list_installment_plans(request.user),
            "recurrences": [(r, r.next_date(today)) for r in recurrences],
            "monthly_expense": monthly(TransactionKind.EXPENSE),
            "monthly_income": monthly(TransactionKind.INCOME),
        },
    )


def _render_form(
    request: AuthenticatedHttpRequest, form: RecurrenceForm, rec: Recurrence | None
) -> HttpResponse:
    context: dict[str, Any] = {
        "form": form,
        "recurrence": rec,
        "form_action": (
            reverse("planning:recurrence_update", args=[rec.pk])
            if rec
            else reverse("planning:recurrence_create")
        ),
        "form_title": "Editar recorrência" if rec else "Nova recorrência",
        "kind": form["kind"].value() or TransactionKind.EXPENSE,
        "kinds": TransactionKind.choices,
        "category_groups": category_groups(request.user, form),
        "selected_category": str(form["category"].value() or ""),
        "frequent_tags": frequent_tags(request.user),
    }
    return render_drawer(
        request, "planning/_recurrence_form.html", context, title=context["form_title"]
    )


@require_http_methods(["GET", "POST"])
def recurrence_create(request: AuthenticatedHttpRequest) -> HttpResponse:
    user = request.user
    if request.method == "GET":
        initial = {"account": last_used_account(user)}
        return _render_form(request, RecurrenceForm(user=user, initial=initial), None)
    form = RecurrenceForm(request.POST, user=user)
    if form.is_valid():
        try:
            rec = create_recurrence(user, form.to_recurrence_data(), today=timezone.localdate())
        except ValidationError as error:
            form.add_service_errors(error)
        else:
            return done(request, f"Recorrência “{rec.label}” criada.", fallback=INDEX)
    return _render_form(request, form, None)


@require_http_methods(["GET", "POST"])
def recurrence_update(request: AuthenticatedHttpRequest, pk: uuid.UUID) -> HttpResponse:
    rec = get_object_or_404(list_recurrences(request.user), pk=pk)
    if request.method == "GET":
        return _render_form(request, RecurrenceForm(user=request.user, recurrence=rec), rec)
    form = RecurrenceForm(request.POST, user=request.user, recurrence=rec)
    if form.is_valid():
        try:
            update_recurrence(rec, form.to_recurrence_data(), today=timezone.localdate())
        except ValidationError as error:
            form.add_service_errors(error)
        else:
            return done(request, "Recorrência atualizada.", fallback=INDEX)
    return _render_form(request, form, rec)


@require_POST
def recurrence_toggle(request: AuthenticatedHttpRequest, pk: uuid.UUID) -> HttpResponse:
    rec = get_object_or_404(list_recurrences(request.user), pk=pk)
    set_recurrence_active(rec, active=not rec.is_active, today=timezone.localdate())
    message = "Recorrência retomada." if rec.is_active else "Recorrência pausada."
    return done(request, message, fallback=INDEX)


@require_POST
def recurrence_delete(request: AuthenticatedHttpRequest, pk: uuid.UUID) -> HttpResponse:
    rec = get_object_or_404(list_recurrences(request.user), pk=pk)
    label = rec.label
    delete_recurrence(rec)
    return done(request, f"Recorrência “{label}” excluída.", fallback=INDEX)


@require_http_methods(["GET", "POST"])
def installment_update(request: AuthenticatedHttpRequest, pk: uuid.UUID) -> HttpResponse:
    plan = get_object_or_404(list_installment_plans(request.user), pk=pk)
    form = InstallmentPlanForm(request.POST or None, user=request.user, plan=plan)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            update_installment_plan(
                plan,
                description=d["description"],
                category=d["category"],
                tag_names=form.tag_names(),
                notes=d["notes"],
            )
        except ValidationError as error:
            apply_service_errors(form, error)
        else:
            return done(request, "Compra parcelada atualizada.", fallback=INDEX)
    return render_drawer(
        request,
        "planning/_installment_form.html",
        {"form": form, "plan": plan},
        title="Compra parcelada",
    )


@require_POST
def installment_delete(request: AuthenticatedHttpRequest, pk: uuid.UUID) -> HttpResponse:
    plan = get_object_or_404(list_installment_plans(request.user), pk=pk)
    label = plan.description
    delete_installment_plan(plan)
    return done(request, f"Compra parcelada “{label}” excluída.", fallback=INDEX)


def _budget_form(
    request: AuthenticatedHttpRequest, form: BudgetForm, budget: Budget | None
) -> HttpResponse:
    return render_drawer(
        request,
        "planning/_budget_form.html",
        {"form": form, "budget": budget},
        title="Editar orçamento" if budget else "Novo orçamento",
    )


@require_http_methods(["GET", "POST"])
def budget_create(request: AuthenticatedHttpRequest) -> HttpResponse:
    form = BudgetForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        try:
            budget = create_budget(
                request.user,
                category=form.cleaned_data.get("category"),
                amount_cents=form.cleaned_data["amount"],
            )
        except ValidationError as error:
            apply_service_errors(form, error, {"amount_cents": "amount"})
        else:
            return done(request, f"Orçamento de “{budget.label}” criado.", fallback=INDEX)
    return _budget_form(request, form, None)


@require_http_methods(["GET", "POST"])
def budget_update(request: AuthenticatedHttpRequest, pk: uuid.UUID) -> HttpResponse:
    budget = get_object_or_404(list_budgets(request.user), pk=pk)
    form = BudgetForm(request.POST or None, user=request.user, budget=budget)
    if request.method == "POST" and form.is_valid():
        try:
            update_budget(budget, amount_cents=form.cleaned_data["amount"])
        except ValidationError as error:
            apply_service_errors(form, error, {"amount_cents": "amount"})
        else:
            return done(request, "Orçamento atualizado.", fallback=INDEX)
    return _budget_form(request, form, budget)


@require_POST
def budget_delete(request: AuthenticatedHttpRequest, pk: uuid.UUID) -> HttpResponse:
    budget = get_object_or_404(list_budgets(request.user), pk=pk)
    label = budget.label
    delete_budget(budget)
    return done(request, f"Orçamento de “{label}” excluído.", fallback=INDEX)


def projection(request: AuthenticatedHttpRequest) -> HttpResponse:
    """Saldo previsto por conta (fim de cada mês), alertas de saldo negativo e faturas."""
    today = timezone.localdate()
    materialize(request.user, today=today)
    include_variable = request.GET.get("variavel") != "0"
    data = projection_data(request.user, today, include_variable=include_variable)
    proj = data.projection
    end = proj.days[-1]
    checkpoints = [today]
    month = today
    while (month_last := month_end(month)) <= end:
        if month_last != today:
            checkpoints.append(month_last)
        month = add_months(month_start(month), 1)

    rows = [
        {"account": a, "values": [proj.balance_on(a.pk, d) for d in checkpoints]}
        for a in data.accounts
    ]
    in_accounts = [a for a in data.accounts if not a.is_liability]

    def total(accounts: list[Any], d: datetime.date) -> int:
        return sum(proj.balance_on(a.pk, d) for a in accounts)

    alerts = []
    for account in in_accounts:
        first = proj.first_negative(account.pk)
        if first is not None:
            low_day, low = proj.lowest(account.pk)
            alerts.append({"account": account, "first": first, "low_day": low_day, "low": low})
    names = {a.pk: a for a in data.accounts}
    chart = {
        "labels": [f"{d:%d/%m}" for d in proj.days],
        "series": [
            {
                "key": "previous",
                "label": "Saldo líquido (com cartões)",
                "values": [total(data.accounts, d) for d in proj.days],
            },
            {
                "key": "current",
                "label": "Em contas (sem cartões)",
                "values": [total(in_accounts, d) for d in proj.days],
            },
        ],
    }
    return render(
        request,
        "planning/projection.html",
        {
            "checkpoints": checkpoints,
            "rows": rows,
            "totals": {
                "accounts": [total(in_accounts, d) for d in checkpoints],
                "net": [total(data.accounts, d) for d in checkpoints],
            },
            "alerts": alerts,
            "statements": [(names[s.card_id], s) for s in proj.statements],
            "unconfigured_cards": data.unconfigured_cards,
            "chart": chart,
            "end": end,
            "today": today,
            "include_variable": include_variable,
            "variable": data.variable,
        },
    )
