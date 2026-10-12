from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "apps.core"
    label = "core"

    def ready(self) -> None:
        from . import dev_autologin  # noqa: F401 — registra o system check
