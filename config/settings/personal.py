"""Uso real e pessoal: o Moneta roda no seu PC e o celular acessa pela rede privada do Tailscale.

- Servidor: waitress em 127.0.0.1:8000 (`scripts/serve.ps1`); o `tailscale serve` publica esse
  endereço só dentro da sua rede Tailscale, com HTTPS (necessário para instalar como app).
- Banco real (`db.sqlite3`) por padrão; DEBUG desligado; login automático impossível.
- Estáticos servidos pelo whitenoise (rode `collectstatic`; o script já faz).

No .env: DJANGO_SECRET_KEY e MONETA_HOSTS (ex.: meu-pc.tail1234.ts.net).
"""

from .base import *  # noqa: F403
from .base import BASE_DIR, MIDDLEWARE, database, env

DEBUG = False

DATABASES = {"default": database(f"sqlite:///{BASE_DIR / 'db.sqlite3'}")}
DEMO_DATABASE = "demo" in str(DATABASES["default"]["NAME"])

# Nome da máquina no Tailscale (o `tailscale serve` mostra a URL https://<nome>.ts.net).
_HOSTS: list[str] = env.list("MONETA_HOSTS", default=[])
ALLOWED_HOSTS = ["localhost", "127.0.0.1", *_HOSTS]
CSRF_TRUSTED_ORIGINS = [f"https://{host}" for host in _HOSTS]

# O tráfego no Tailscale já é criptografado e o `tailscale serve` entrega HTTPS. Cookies
# "secure" quebrariam o acesso pelo próprio PC em http://localhost, então ficam opcionais.
SESSION_COOKIE_SECURE = env.bool("MONETA_SECURE_COOKIES", default=False)
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE

# Arquivos estáticos com nome versionado (cache longo) servidos pela própria aplicação.
MIDDLEWARE = [
    MIDDLEWARE[0],  # SecurityMiddleware
    "whitenoise.middleware.WhiteNoiseMiddleware",
    *MIDDLEWARE[1:],
]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": BASE_DIR / "moneta.log",
            "maxBytes": 1_000_000,
            "backupCount": 3,
            "encoding": "utf-8",
        }
    },
    "root": {"handlers": ["file"], "level": "WARNING"},
}
