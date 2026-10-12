import uuid
from typing import Any

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from apps.core.drawer import done, render_drawer
from apps.core.forms import apply_service_errors
from apps.core.http import AuthenticatedHttpRequest

from .domain.products import PRESETS, MissingRateError
from .forms import NO_YIELD_KEY, ProductForm, PurchaseForm, RateOverrideForm, SimulationForm
from .market import MarketDataError, update_rates
from .models import InvestmentProduct
from .selectors import (
    analyze_purchase,
    effective_rates,
    list_products,
    product_rows,
    run_simulations,
    simulation_options,
)
from .services import create_product, delete_product, set_rate_override, update_product

INDEX = "investments:index"


def index(request: AuthenticatedHttpRequest) -> HttpResponse:
    today = timezone.localdate()
    rates = effective_rates(request.user)
    return render(
        request,
        "investments/index.html",
        {
            "rates": [
                {
                    "rate": rate,
                    "manual_text": f"{rate.manual:f}".replace(".", ",") if rate.manual else "",
                    "stale": rate.is_stale(today),
                }
                for rate in rates.values()
            ],
            "products": product_rows(request.user, today),
            "presets": PRESETS,
        },
    )


@require_POST
def rates_update(request: AuthenticatedHttpRequest) -> HttpResponse:
    try:
        latest = update_rates()
    except MarketDataError as exc:
        messages.error(request, str(exc))
    else:
        summary = ", ".join(
            f"{r.get_series_display()} {r.annual_percent:.2f}%".replace(".", ",")
            for r in latest.values()
        )
        messages.success(request, f"Taxas atualizadas: {summary}.")
    return redirect(INDEX)


@require_POST
def rate_override(request: AuthenticatedHttpRequest) -> HttpResponse:
    form = RateOverrideForm(request.POST)
    if not form.is_valid():
        messages.error(request, " ".join(str(e) for errors in form.errors.values() for e in errors))
        return redirect(INDEX)
    value = form.cleaned_data["value"]
    try:
        set_rate_override(request.user, form.cleaned_data["series"], value)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        text = "Taxa salva." if value is not None else "Voltou a usar a taxa do Banco Central."
        messages.success(request, text)
    return redirect(INDEX)


def _product_drawer(
    request: AuthenticatedHttpRequest, form: ProductForm, product: InvestmentProduct | None
) -> HttpResponse:
    return render_drawer(
        request,
        "investments/_product_form.html",
        {"form": form, "product": product, "presets": PRESETS},
        title="Editar produto" if product else "Novo produto",
    )


@require_http_methods(["GET", "POST"])
def product_create(request: AuthenticatedHttpRequest) -> HttpResponse:
    if request.method == "GET":
        initial = ProductForm.preset_initial(request.GET.get("modelo", "")) or {}
        return _product_drawer(request, ProductForm(initial=initial), None)
    form = ProductForm(request.POST)
    if form.is_valid():
        try:
            product = create_product(request.user, form.to_data())
        except ValidationError as error:
            apply_service_errors(form, error, {"rate_percent": "rate"})
        else:
            return done(request, f"Produto “{product}” criado.", fallback=INDEX)
    return _product_drawer(request, form, None)


@require_http_methods(["GET", "POST"])
def product_update(request: AuthenticatedHttpRequest, pk: uuid.UUID) -> HttpResponse:
    product = get_object_or_404(list_products(request.user), pk=pk)
    form = ProductForm(request.POST or None, product=product)
    if request.method == "POST" and form.is_valid():
        try:
            update_product(product, form.to_data())
        except ValidationError as error:
            apply_service_errors(form, error, {"rate_percent": "rate"})
        else:
            return done(request, "Produto atualizado.", fallback=INDEX)
    return _product_drawer(request, form, product)


@require_POST
def product_delete(request: AuthenticatedHttpRequest, pk: uuid.UUID) -> HttpResponse:
    product = get_object_or_404(list_products(request.user), pk=pk)
    name = product.name
    delete_product(product)
    return done(request, f"Produto “{name}” excluído.", fallback=INDEX)


def simulator(request: AuthenticatedHttpRequest) -> HttpResponse:
    """GET com os parâmetros na URL: a simulação pode ser guardada/compartilhada como link."""
    today = timezone.localdate()
    options = simulation_options(request.user)
    choices = [(o.key, o.label) for o in options]
    form = SimulationForm(
        request.GET or None,
        choices=choices,
        initial={"produto": [key for key, _ in choices], "meses": 24},
    )
    rows: list[dict[str, Any]] = []
    chart = None
    if form.is_bound and form.is_valid():
        d = form.cleaned_data
        chosen = set(d["produto"]) or {key for key, _ in choices}
        results = run_simulations(
            request.user,
            [o for o in options if o.key in chosen],
            start=today,
            initial_cents=d.get("inicial") or 0,
            monthly_cents=d.get("aporte") or 0,
            months=d["meses"],
        )
        done = [(r, r.simulation) for r in results if r.simulation is not None]
        best = max(done, key=lambda pair: pair[1].final.valuation.net_cents)[0] if done else None
        rows = [{"row": r, "best": r is best} for r in results]
        if done:
            chart = {
                "labels": [f"{p.day:%m/%y}" for p in done[0][1].points],
                "series": [
                    {
                        "key": f"s{index % 8 + 1}",
                        "label": r.option.label,
                        "values": [p.valuation.net_cents for p in sim.points],
                    }
                    for index, (r, sim) in enumerate(done)
                ],
            }
    return render(
        request,
        "investments/simulator.html",
        {
            "form": form,
            "rows": rows,
            "chart": chart,
            "has_products": any(not o.key.startswith("ref-") for o in options),
            "rates": effective_rates(request.user),
        },
    )


def purchase(request: AuthenticatedHttpRequest) -> HttpResponse:
    """Compara formas de pagar uma compra (parâmetros na URL)."""
    today = timezone.localdate()
    options = simulation_options(request.user)
    form = PurchaseForm(
        request.GET or None,
        choices=[(o.key, o.label) for o in options],
        initial={"produto": options[0].key if options else NO_YIELD_KEY},
    )
    analysis = None
    missing = ""
    if form.is_bound and form.is_valid():
        try:
            analysis = analyze_purchase(request.user, form.cleaned_data, start=today)
        except MissingRateError as exc:
            missing = str(exc)
    return render(
        request,
        "investments/purchase.html",
        {"form": form, "analysis": analysis, "missing": missing},
    )
