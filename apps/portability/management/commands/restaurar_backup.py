from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import connection
from django.utils import timezone

from apps.portability.backup import BackupError, restore_sqlite


class Command(BaseCommand):
    help = (
        "Restaura o banco SQLite a partir de um backup. Guarda antes uma cópia do banco atual. "
        "Pare o servidor (runserver) antes de restaurar."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("arquivo", type=Path, help="Arquivo .sqlite3 gerado pelo backup.")
        parser.add_argument(
            "--confirmar",
            action="store_true",
            help="Sem esta opção, só mostra o que seria feito.",
        )
        parser.add_argument(
            "--forcar",
            action="store_true",
            help="Permite restaurar backup de outro banco (ex.: demo no banco real).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if connection.vendor != "sqlite":
            raise CommandError("restaurar_backup só funciona com SQLite.")
        name = str(connection.settings_dict["NAME"])
        if name == ":memory:" or name.startswith("file:"):
            raise CommandError("Banco em memória não tem arquivo para restaurar.")
        target = Path(name)
        backup: Path = options["arquivo"]
        if not backup.name.startswith(f"{target.stem}-") and not options["forcar"]:
            raise CommandError(
                f"O backup {backup.name} não parece ser do banco {target.name}. "
                "Use --forcar se tiver certeza."
            )
        if not options["confirmar"]:
            self.stdout.write(
                f"Restauraria {backup} sobre {target} (uma cópia do banco atual seria guardada "
                "em backups/). Rode de novo com --confirmar, com o servidor parado."
            )
            return
        connection.close()
        try:
            result = restore_sqlite(
                backup,
                target,
                safety_dir=settings.BASE_DIR / "backups",
                now=timezone.localtime(),
            )
        except BackupError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"Banco restaurado a partir de {backup.name}."))
        self.stdout.write(f"O banco anterior foi guardado em {result.safety_copy}.")
