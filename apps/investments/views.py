from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core.http import AuthenticatedHttpRequest

from .forms import RateOverrideForm
from .market import MarketDataError, update_rates
from .selectors import effective_rates
from .services import set_rate_override

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
