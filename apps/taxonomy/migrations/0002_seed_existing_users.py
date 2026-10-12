"""Cria as categorias padrão para usuários que já existiam antes do app `taxonomy`.
Usuários novos recebem as categorias via signal (apps.taxonomy.signals)."""

from typing import Any

from django.conf import settings
from django.db import migrations

from apps.core.names import normalize_name
from apps.taxonomy.defaults import DEFAULT_CATEGORIES


def seed(apps: Any, schema_editor: Any) -> None:
    User = apps.get_model(settings.AUTH_USER_MODEL)
    Category = apps.get_model("taxonomy", "Category")
    for user in User.objects.all():
        existing = set(Category.objects.filter(owner=user).values_list("kind", "normalized_name"))
        Category.objects.bulk_create(
            Category(
                owner=user,
                name=d.name,
                normalized_name=normalize_name(d.name),
                kind=d.kind,
                color=d.color,
                icon=d.icon,
            )
            for d in DEFAULT_CATEGORIES
            if (d.kind, normalize_name(d.name)) not in existing
        )


class Migration(migrations.Migration):
    dependencies = [
        ("taxonomy", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
