"""App instalável (PWA): manifest, service worker e página offline — rotas públicas.

O service worker NÃO guarda páginas com dados em cache: só a página "sem conexão", mostrada
quando o PC que serve o Moneta está desligado ou fora da rede.
"""

import json

from django.contrib.auth.decorators import login_not_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.templatetags.static import static
from django.urls import reverse
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET

THEME = "#0a0a0a"


@require_GET
@login_not_required
def manifest(request: HttpRequest) -> HttpResponse:
    data = {
        "name": "Moneta",
        "short_name": "Moneta",
        "description": "Gestão e planejamento financeiro pessoal",
        "lang": "pt-BR",
        "start_url": reverse("home"),
        "scope": "/",
        "display": "standalone",
        "background_color": THEME,
        "theme_color": THEME,
        "icons": [
            {
                "src": static("icons/pwa-192.png"),
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any maskable",
            },
            {
                "src": static("icons/pwa-512.png"),
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any maskable",
            },
        ],
    }
    return HttpResponse(
        json.dumps(data, ensure_ascii=False), content_type="application/manifest+json"
    )


@require_GET
@login_not_required
@cache_control(no_cache=True)
def service_worker(request: HttpRequest) -> HttpResponse:
    response = render(
        request,
        "pwa/sw.js",
        {"offline_url": reverse("offline")},
        content_type="application/javascript",
    )
    response["Service-Worker-Allowed"] = "/"
    return response


@require_GET
@login_not_required
def offline(request: HttpRequest) -> HttpResponse:
    return render(request, "pwa/offline.html")
