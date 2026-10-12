import uuid
from typing import Any

from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from apps.core.drawer import done, render_drawer
from apps.core.http import AuthenticatedHttpRequest
from apps.ledger.models import TransactionKind
from apps.ledger.selectors import frequent_tags, last_used_account
from apps.ledger.views import category_groups

from .forms import RecurrenceForm
from .models import Recurrence
from .selectors import list_recurrences
from .services import (
    create_recurrence,
    delete_recurrence,
    materialize,
    set_recurrence_active,
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
