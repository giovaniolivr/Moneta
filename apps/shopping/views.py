import uuid
from typing import Any

from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from apps.core.drawer import done, render_drawer
from apps.core.forms import apply_service_errors
from apps.core.http import AuthenticatedHttpRequest

from .forms import ObservationForm, WishItemForm
from .models import WishItem
from .selectors import best_price_series, item_rows, list_items, list_observations, summary
from .services import (
    ObservationData,
    add_observation,
    create_item,
    delete_item,
    delete_observation,
    save_item,
    set_item_done,
)

INDEX = "shopping:index"


def index(request: AuthenticatedHttpRequest) -> HttpResponse:
    rows = item_rows(request.user, timezone.localdate())
    return render(
        request,
        "shopping/index.html",
        {
            "active": [r for r in rows if not r.item.is_done],
            "done_items": [r for r in rows if r.item.is_done],
        },
    )


def _item_drawer(
    request: AuthenticatedHttpRequest, form: WishItemForm, item: WishItem | None
) -> HttpResponse:
    return render_drawer(
        request,
        "shopping/_item_form.html",
        {"form": form, "item": item},
        title="Editar item" if item else "Novo item",
    )


@require_http_methods(["GET", "POST"])
def item_create(request: AuthenticatedHttpRequest) -> HttpResponse:
    form = WishItemForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            item = create_item(
                request.user, name=d["name"], target_cents=d.get("target"), notes=d["notes"]
            )
        except ValidationError as error:
            apply_service_errors(form, error, {"target_cents": "target"})
        else:
            return done(
                request, f"“{item}” entrou na lista. Registre o primeiro preço.", fallback=INDEX
            )
    return _item_drawer(request, form, None)


@require_http_methods(["GET", "POST"])
def item_update(request: AuthenticatedHttpRequest, pk: uuid.UUID) -> HttpResponse:
    item = get_object_or_404(list_items(request.user), pk=pk)
    form = WishItemForm(request.POST or None, item=item)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            save_item(item, name=d["name"], target_cents=d.get("target"), notes=d["notes"])
        except ValidationError as error:
            apply_service_errors(form, error, {"target_cents": "target"})
        else:
            return done(request, "Item atualizado.", fallback=INDEX)
    return _item_drawer(request, form, item)


@require_POST
def item_toggle_done(request: AuthenticatedHttpRequest, pk: uuid.UUID) -> HttpResponse:
    item = get_object_or_404(list_items(request.user), pk=pk)
    set_item_done(item, done=not item.is_done)
    message = "Item marcado como resolvido." if item.is_done else "Item voltou para a lista."
    return done(request, message, fallback=INDEX)


@require_POST
def item_delete(request: AuthenticatedHttpRequest, pk: uuid.UUID) -> HttpResponse:
    item = get_object_or_404(list_items(request.user), pk=pk)
    name = item.name
    delete_item(item)
    return done(request, f"“{name}” saiu da lista.", fallback=INDEX)


def item_detail(request: AuthenticatedHttpRequest, pk: uuid.UUID) -> HttpResponse:
    item = get_object_or_404(list_items(request.user), pk=pk)
    series = best_price_series(item)
    chart: dict[str, Any] | None = None
    if len(series) >= 2:
        chart = {
            "labels": [f"{day:%d/%m}" for day, _ in series],
            "series": [
                {"key": "current", "label": "Melhor preço", "values": [v for _, v in series]}
            ],
        }
    return render(
        request,
        "shopping/item_detail.html",
        {
            "item": item,
            "summary": summary(item, timezone.localdate()),
            "observations": item.observations.all(),
            "chart": chart,
            "series": series,
        },
    )


@require_http_methods(["GET", "POST"])
def observation_create(request: AuthenticatedHttpRequest, pk: uuid.UUID) -> HttpResponse:
    item = get_object_or_404(list_items(request.user), pk=pk)
    form = ObservationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            obs = add_observation(
                item,
                ObservationData(
                    price_cents=d["price"],
                    url=d.get("url") or "",
                    store=d.get("store") or "",
                    shipping_cents=d.get("shipping") or 0,
                    installments=d.get("installments"),
                    observed_on=d["observed_on"],
                ),
            )
        except ValidationError as error:
            apply_service_errors(
                form,
                error,
                {"price_cents": "price", "shipping_cents": "shipping"},
            )
        else:
            return done(
                request,
                f"Preço registrado em {obs.store}.",
                fallback=reverse("shopping:item_detail", args=[item.pk]),
            )
    return render_drawer(
        request,
        "shopping/_observation_form.html",
        {"form": form, "item": item},
        title=f"Preço de {item.name}",
    )


@require_POST
def observation_delete(request: AuthenticatedHttpRequest, pk: uuid.UUID) -> HttpResponse:
    obs = get_object_or_404(list_observations(request.user), pk=pk)
    item_pk = obs.item_id
    delete_observation(obs)
    return done(
        request,
        "Registro de preço excluído.",
        fallback=reverse("shopping:item_detail", args=[item_pk]),
    )
