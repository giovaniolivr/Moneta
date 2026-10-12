from django.http import HttpRequest

from apps.users.models import User


class AuthenticatedHttpRequest(HttpRequest):
    """Tipo para views protegidas pelo LoginRequiredMiddleware (todas, exceto login)."""

    user: User
