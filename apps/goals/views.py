import uuid

from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods, require_POST

from apps.core.drawer import done, render_drawer
from apps.core.forms import apply_service_errors
from apps.core.http import AuthenticatedHttpRequest

from .forms import GoalForm
from .models import Goal
from .selectors import list_goals
from .services import create_goal, delete_goal, update_goal

INDEX = "planning:index"
ERRORS: dict[str, str | None] = {"target_cents": "target"}


def _drawer(request: AuthenticatedHttpRequest, form: GoalForm, goal: Goal | None) -> HttpResponse:
    return render_drawer(
        request,
        "goals/_goal_form.html",
        {"form": form, "goal": goal},
        title="Editar meta" if goal else "Nova meta",
    )


@require_http_methods(["GET", "POST"])
def goal_create(request: AuthenticatedHttpRequest) -> HttpResponse:
    # Pré-preenchimento vindo de outras telas (ex.: estratégias de compra): ?nome=&alvo=&prazo=
    initial = {
        "name": request.GET.get("nome", "")[:80],
        "target": request.GET.get("alvo", "").replace(".", ","),
        "deadline": request.GET.get("prazo", ""),
    }
    form = GoalForm(
        request.POST or None,
        user=request.user,
        initial={k: v for k, v in initial.items() if v},
    )
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            goal = create_goal(
                request.user,
                name=d["name"],
                target_cents=d["target"],
                account=d["account"],
                deadline=d.get("deadline"),
            )
        except ValidationError as error:
            apply_service_errors(form, error, ERRORS)
        else:
            return done(request, f"Meta “{goal}” criada.", fallback=INDEX)
    return _drawer(request, form, None)


@require_http_methods(["GET", "POST"])
def goal_update(request: AuthenticatedHttpRequest, pk: uuid.UUID) -> HttpResponse:
    goal = get_object_or_404(list_goals(request.user), pk=pk)
    form = GoalForm(request.POST or None, user=request.user, goal=goal)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            update_goal(
                goal,
                name=d["name"],
                target_cents=d["target"],
                account=d["account"],
                deadline=d.get("deadline"),
            )
        except ValidationError as error:
            apply_service_errors(form, error, ERRORS)
        else:
            return done(request, "Meta atualizada.", fallback=INDEX)
    return _drawer(request, form, goal)


@require_POST
def goal_delete(request: AuthenticatedHttpRequest, pk: uuid.UUID) -> HttpResponse:
    goal = get_object_or_404(list_goals(request.user), pk=pk)
    name = goal.name
    delete_goal(goal)
    return done(request, f"Meta “{name}” excluída. O dinheiro na conta não muda.", fallback=INDEX)
