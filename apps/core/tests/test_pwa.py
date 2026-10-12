"""App instalável: manifest, service worker e página offline são públicos e não vazam dados."""

import json
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

pytestmark = pytest.mark.django_db


def test_manifest(client: Client) -> None:
    response = client.get(reverse("manifest"))
    assert response.status_code == 200
    assert response["Content-Type"] == "application/manifest+json"
    data = json.loads(response.content)
    assert (data["name"], data["display"], data["start_url"]) == ("Moneta", "standalone", "/")
    sizes = {icon["sizes"] for icon in data["icons"]}
    assert sizes == {"192x192", "512x512"}


def test_service_worker_only_caches_offline_page(client: Client) -> None:
    response = client.get(reverse("service_worker"))
    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/javascript")
    assert response["Service-Worker-Allowed"] == "/"
    body = response.content.decode()
    assert 'const OFFLINE_URL = "/offline/"' in body
    assert "cache.add(OFFLINE_URL)" in body
    assert "cache.put" not in body  # nunca guarda respostas com dados


def test_offline_page_is_public_and_generic(client: Client, user: Any) -> None:
    html = client.get(reverse("offline")).content.decode()
    assert "O Moneta não respondeu" in html
    assert "R$" not in html


def test_pages_link_manifest(client: Client) -> None:
    html = client.get(reverse("login")).content.decode()
    assert f'rel="manifest" href="{reverse("manifest")}"' in html
    assert "apple-touch-icon" in html
