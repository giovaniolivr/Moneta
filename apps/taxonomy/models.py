from typing import Any

from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import OwnedModel
from apps.core.names import InvalidNameError, clean_display_name, normalize_name


class CategoryKind(models.TextChoices):
    EXPENSE = "expense", "Despesa"
    INCOME = "income", "Receita"


class ColorSlot(models.TextChoices):
    """Posição na paleta categórica do tema — a cor real vem dos tokens CSS de cada tema,
    garantindo contraste no escuro e no claro."""

    C1 = "1", "Cor 1"
    C2 = "2", "Cor 2"
    C3 = "3", "Cor 3"
    C4 = "4", "Cor 4"
    C5 = "5", "Cor 5"
    C6 = "6", "Cor 6"
    C7 = "7", "Cor 7"
    C8 = "8", "Cor 8"


class Category(OwnedModel):
    name = models.CharField("nome", max_length=60)
    normalized_name = models.CharField(max_length=60, editable=False, blank=True)  # calculado
    kind = models.CharField("tipo", max_length=10, choices=CategoryKind.choices)
    color = models.CharField("cor", max_length=2, choices=ColorSlot.choices, default=ColorSlot.C1)
    icon = models.CharField("ícone", max_length=40, blank=True)
    is_archived = models.BooleanField("arquivada", default=False)

    class Meta:
        verbose_name = "categoria"
        verbose_name_plural = "categorias"
        ordering = ["kind", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "kind", "normalized_name"],
                name="category_unique_name_per_owner_kind",
                violation_error_message="Já existe uma categoria com este nome.",
            ),
        ]
        indexes = [models.Index(fields=["owner", "kind", "is_archived"])]

    def __str__(self) -> str:
        return self.name

    def normalize(self) -> None:
        self.name = clean_display_name(self.name)
        self.normalized_name = normalize_name(self.name)

    def clean(self) -> None:
        try:
            self.normalize()
        except InvalidNameError as exc:
            raise ValidationError({"name": str(exc)}) from exc

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.normalize()
        super().save(*args, **kwargs)
