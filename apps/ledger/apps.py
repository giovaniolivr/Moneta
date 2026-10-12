from django.apps import AppConfig


class LedgerConfig(AppConfig):
    name = "apps.ledger"
    label = "ledger"
    verbose_name = "Contas e movimentações"

    def ready(self) -> None:
        from . import signals  # noqa: F401
