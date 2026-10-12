"""Contrato de segurança: nenhuma página é acessível sem login, exceto as de autenticação."""

import pytest
from django.urls import URLPattern, URLResolver, get_resolver, reverse

PUBLIC_URL_NAMES = {"login"}


def _named_routes(patterns: list[URLPattern | URLResolver], ns: str = "") -> list[str]:
    names: list[str] = []
    for p in patterns:
        if isinstance(p, URLResolver):
            if p.namespace == "admin":  # o admin tem seu próprio controle de acesso
                continue
            prefix = f"{ns}{p.namespace}:" if p.namespace else ns
            names += _named_routes(p.url_patterns, prefix)
        elif p.name and not p.pattern.regex.groupindex:
            names.append(f"{ns}{p.name}")
    return names


@pytest.mark.django_db
@pytest.mark.parametrize("name", sorted(set(_named_routes(get_resolver().url_patterns))))
def test_routes_require_login(client, name: str) -> None:
    response = client.get(reverse(name))
    if name in PUBLIC_URL_NAMES:
        assert response.status_code == 200
    else:
        assert response.status_code in {302, 405}
        if response.status_code == 302:
            assert response.url.startswith(reverse("login"))


@pytest.mark.django_db
def test_login_then_home(client, django_user_model) -> None:
    django_user_model.objects.create_user(username="ana", password="senha-forte-123")
    response = client.post(
        reverse("login"), {"username": "ana", "password": "senha-forte-123"}, follow=True
    )
    assert response.status_code == 200
    assert response.request["PATH_INFO"] == reverse("home")
    assert "Boas-vindas ao Moneta" in response.content.decode()


def test_security_headers(client) -> None:
    response = client.get(reverse("login"))
    assert "default-src 'self'" in response["Content-Security-Policy"]
    assert response["X-Frame-Options"] == "DENY"
    assert response["X-Content-Type-Options"] == "nosniff"
