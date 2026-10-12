from django.apps import AppConfig


class TaxonomyConfig(AppConfig):
    name = "apps.taxonomy"
    label = "taxonomy"
    verbose_name = "Categorias e tags"

    def ready(self) -> None:
        from . import signals  # noqa: F401
