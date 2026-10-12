"""Cria a conta padrão para usuários que já existiam antes do app `ledger`.
Usuários novos recebem a conta via signal (apps.ledger.signals)."""

from typing import Any

from django.conf import settings
from django.db import migrations

from apps.core.names import normalize_name

DEFAULT_ACCOUNT_NAME = "Principal"


def seed(apps: Any, schema_editor: Any) -> None:
    User = apps.get_model(settings.AUTH_USER_MODEL)
    Account = apps.get_model("ledger", "Account")
    for user in User.objects.exclude(pk__in=Account.objects.values("owner")):
        Account.objects.create(
            owner=user,
            name=DEFAULT_ACCOUNT_NAME,
            normalized_name=normalize_name(DEFAULT_ACCOUNT_NAME),
            kind="checking",
        )


class Migration(migrations.Migration):
    dependencies = [
        ("ledger", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
