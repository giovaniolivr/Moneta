from typing import Any

import pytest
from django.conf import settings
from django.test import Client, override_settings
from django.urls import reverse

from apps.core.dev_autologin import MIDDLEWARE_PATH, check_not_in_production

AUTH_MW = "django.contrib.auth.middleware.AuthenticationMiddleware"
WITH_AUTOLOGIN = [
    m for mw in settings.MIDDLEWARE for m in ([mw, MIDDLEWARE_PATH] if mw == AUTH_MW else [mw])
]

pytestmark = pytest.mark.django_db


@pytest.fixture
def autologin(settings: Any) -> Any:
    settings.DEBUG = True
    settings.DEV_AUTO_LOGIN = True
    settings.DEV_AUTO_LOGIN_USERNAME = ""
    settings.MIDDLEWARE = WITH_AUTOLOGIN
    return settings


@pytest.mark.usefixtures("autologin")
class TestEnabled:
    def test_localhost_goes_straight_to_home(self, client: Client, user: Any) -> None:
        response = client.get(reverse("home"))
        assert response.status_code == 200
        assert response.wsgi_request.user == user
        assert "Sair" not in response.content.decode()  # sem botão de sair

    def test_prefers_superuser(self, client: Client, user: Any, django_user_model: Any) -> None:
        admin = django_user_model.objects.create_superuser("admin", password="senha-forte-123")
        assert client.get(reverse("home")).wsgi_request.user == admin

    def test_username_setting(
        self, client: Client, user: Any, other_user: Any, autologin: Any
    ) -> None:
        autologin.DEV_AUTO_LOGIN_USERNAME = "bruno"
        assert client.get(reverse("home")).wsgi_request.user == other_user

    def test_remote_address_still_needs_login(self, user: Any) -> None:
        response = Client(REMOTE_ADDR="192.168.0.10").get(reverse("home"))
        assert response.status_code == 302
        assert response["Location"].startswith(reverse("login"))

    def test_without_users_shows_login(self, client: Client) -> None:
        assert client.get(reverse("home")).status_code == 302


def test_never_logs_in_without_debug(client: Client, user: Any, autologin: Any) -> None:
    autologin.DEBUG = False
    assert client.get(reverse("home")).status_code == 302


def test_can_be_turned_off(client: Client, user: Any, autologin: Any) -> None:
    autologin.DEV_AUTO_LOGIN = False
    assert client.get(reverse("home")).status_code == 302


def test_system_check_blocks_middleware_in_production() -> None:
    with override_settings(DEBUG=False, MIDDLEWARE=WITH_AUTOLOGIN):
        assert [e.id for e in check_not_in_production(None)] == ["moneta.E001"]
    with override_settings(DEBUG=True, MIDDLEWARE=WITH_AUTOLOGIN):
        assert check_not_in_production(None) == []


def test_not_enabled_in_base_settings() -> None:
    from config.settings import base, prod

    assert MIDDLEWARE_PATH not in base.MIDDLEWARE
    assert MIDDLEWARE_PATH not in prod.MIDDLEWARE
