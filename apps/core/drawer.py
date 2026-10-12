"""Suporte às telas que abrem no painel lateral (static/js/drawer.js).

Contrato com o JavaScript:
- O painel pede a URL com o header `X-Moneta-Fragment: 1` e recebe só o fragmento HTML.
- `204` + `X-Moneta-Event`: a ação terminou → o painel fecha e a página recarrega.
- `200` + `X-Moneta-Event`: algo mudou, mas o painel continua aberto (ex.: apelido adicionado);
  a página recarrega quando o painel for fechado.
Sem JavaScript, as mesmas URLs funcionam como páginas inteiras (`layouts/drawer_page.html`).
"""

from typing import Any

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme

FRAGMENT_HEADER = "X-Moneta-Fragment"
EVENT_HEADER = "X-Moneta-Event"


def is_fragment(request: HttpRequest) -> bool:
    return request.headers.get(FRAGMENT_HEADER) == "1"


def render_drawer(
    request: HttpRequest,
    fragment_template: str,
    context: dict[str, Any],
    *,
    title: str,
    changed: bool = False,
) -> HttpResponse:
    """Renderiza o fragmento (painel) ou a página inteira equivalente."""
    context = {**context, "drawer_title": title, "fragment_template": fragment_template}
    if is_fragment(request):
        response = render(request, fragment_template, context)
    else:
        response = render(request, "layouts/drawer_page.html", context)
    if changed:
        response[EVENT_HEADER] = "changed"
    return response


def safe_next(request: HttpRequest) -> str:
    next_url = request.POST.get("next") or request.GET.get("next") or ""
    if url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return next_url
    return ""


def done(
    request: HttpRequest, message: str, *, fallback: str, extra_tags: str = ""
) -> HttpResponse:
    """Ação concluída: fecha o painel (204) ou redireciona (`next` seguro ou `fallback`).

    `extra_tags="undo"` mostra o botão "Desfazer" na mensagem (ver `layouts/app.html`)."""
    messages.success(request, message, extra_tags=extra_tags)
    if is_fragment(request):
        response = HttpResponse(status=204)
        response[EVENT_HEADER] = "done"
        return response
    return redirect(safe_next(request) or fallback)
