"""Desenvolvimento local (`runserver`)."""

from .base import *  # noqa: F403
from .base import MIDDLEWARE, env

DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]"]

# Login automático (só localhost; ver apps/core/dev_autologin.py). Desative com DEV_AUTO_LOGIN=0.
DEV_AUTO_LOGIN = env.bool("DEV_AUTO_LOGIN", default=True)
DEV_AUTO_LOGIN_USERNAME = env("DEV_AUTO_LOGIN_USERNAME", default="")
_auth = MIDDLEWARE.index("django.contrib.auth.middleware.AuthenticationMiddleware") + 1
MIDDLEWARE = [
    *MIDDLEWARE[:_auth],
    "apps.core.dev_autologin.DevAutoLoginMiddleware",
    *MIDDLEWARE[_auth:],
]
