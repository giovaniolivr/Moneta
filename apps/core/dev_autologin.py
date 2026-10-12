"""Login automático para uso local (`runserver`), enquanto o app é de um único usuário.

Ativo somente quando TODAS as condições valem:
- `DEBUG=True` (settings.dev) e `DEV_AUTO_LOGIN=True`;
- requisição vinda do próprio computador (127.0.0.1 / ::1).

Em qualquer outro cenário o fluxo normal de login continua exigido. Um system check impede que o
middleware fique ativo com `DEBUG=False`.
"""

from collections.abc import Callable
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.core.checks import Error, register
from django.http import HttpRequest, HttpResponse

MIDDLEWARE_PATH = "apps.core.dev_autologin.DevAutoLoginMiddleware"
LOOPBACK = {"127.0.0.1", "::1"}


def is_enabled() -> bool:
    return bool(
        settings.DEBUG
        and getattr(settings, "DEV_AUTO_LOGIN", False)
        and MIDDLEWARE_PATH in settings.MIDDLEWARE
    )


def _pick_user() -> Any:
    User = get_user_model()
    username = getattr(settings, "DEV_AUTO_LOGIN_USERNAME", "")
    active = User.objects.filter(is_active=True)
    if username:
        return active.filter(username=username).first()
    return active.order_by("-is_superuser", "date_joined").first()


class DevAutoLoginMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if (
            is_enabled()
            and not request.user.is_authenticated
            and request.META.get("REMOTE_ADDR") in LOOPBACK
            and (user := _pick_user()) is not None
        ):
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        return self.get_response(request)


@register("security")
def check_not_in_production(app_configs: Any, **kwargs: Any) -> list[Error]:
    if MIDDLEWARE_PATH in settings.MIDDLEWARE and not settings.DEBUG:
        message = "Login automático de desenvolvimento ativo com DEBUG=False."
        return [Error(message, id="moneta.E001")]
    return []
