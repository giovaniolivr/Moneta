from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.investments.market import MarketDataError, update_rates


class Command(BaseCommand):
    help = "Busca CDI, Selic e IPCA na API SGS do Banco Central e guarda o histórico."

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            latest = update_rates()
        except MarketDataError as exc:
            raise CommandError(str(exc)) from exc
        for rate in latest.values():
            self.stdout.write(self.style.SUCCESS(str(rate)))
