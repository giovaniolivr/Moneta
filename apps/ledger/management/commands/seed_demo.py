from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils import timezone

from apps.ledger.demo import DEMO_PASSWORD, DEMO_USERNAME, seed_demo
from apps.users.models import User


class Command(BaseCommand):
    help = "Cria (ou recria) o usuário de demonstração com ~6 meses de dados fictícios."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--months", type=int, default=6)
        parser.add_argument("--seed", type=int, default=42)
        parser.add_argument(
            "--force",
            action="store_true",
            help="Permite rodar num banco cujo nome não contém 'demo'.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        name = str(settings.DATABASES["default"]["NAME"])
        if "demo" not in name and not options["force"]:
            raise CommandError(
                f"Recusado: o banco atual ({name}) não parece ser de demonstração. "
                "Use o padrão de desenvolvimento (db-demo.sqlite3) ou --force."
            )
        deleted, _ = User.objects.filter(username=DEMO_USERNAME).delete()
        if deleted:
            self.stdout.write("Dados de demonstração anteriores removidos.")
        summary = seed_demo(
            today=timezone.localdate(), months=options["months"], seed=options["seed"]
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Usuário '{DEMO_USERNAME}' (senha '{DEMO_PASSWORD}') criado com "
                f"{summary.transactions} movimentações em {summary.accounts} contas, "
                f"de {summary.first_date:%d/%m/%Y} a {summary.last_date:%d/%m/%Y}."
            )
        )
