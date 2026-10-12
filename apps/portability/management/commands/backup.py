from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import connection
from django.utils import timezone

from apps.portability.backup import BackupError, backup_sqlite


class Command(BaseCommand):
    help = "Faz uma cópia consistente do banco SQLite em uso, com rotação das cópias antigas."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--destino",
            type=Path,
            default=settings.BASE_DIR / "backups",
            help="Pasta das cópias (padrão: backups/ na raiz do projeto).",
        )
        parser.add_argument(
            "--manter", type=int, default=10, help="Quantas cópias deste banco manter."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if connection.vendor != "sqlite":
            raise CommandError("O comando backup só funciona com SQLite. Use a exportação JSON.")
        name = str(connection.settings_dict["NAME"])
        if name == ":memory:" or name.startswith("file:"):
            raise CommandError("Banco em memória não tem arquivo para copiar.")
        try:
            result = backup_sqlite(
                Path(name),
                options["destino"],
                keep=options["manter"],
                now=timezone.localtime(),
            )
        except BackupError as exc:
            raise CommandError(str(exc)) from exc
        size_kb = result.size_bytes / 1024
        self.stdout.write(self.style.SUCCESS(f"Backup criado: {result.path} ({size_kb:.0f} KB)"))
        for old in result.removed:
            self.stdout.write(f"Removida cópia antiga: {old.name}")
