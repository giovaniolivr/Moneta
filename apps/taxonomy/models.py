from django.db import models

from apps.core.models import NormalizedNameModel, OwnedModel


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


class Category(NormalizedNameModel, OwnedModel):
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


class Tag(NormalizedNameModel, OwnedModel):
    """Classificação livre e transversal às categorias ("Delivery", "Uber", "Faculdade").

    O nome de exibição é o da criação; qualquer grafia equivalente ("MERCADO", " mercado ")
    resolve para a mesma tag. Equivalências semânticas ("Supermercado" = "Mercado") só existem
    por ação explícita do usuário, via `TagAlias` (mesclagem ou apelido).
    """

    class Meta:
        verbose_name = "tag"
        verbose_name_plural = "tags"
        ordering = ["normalized_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "normalized_name"],
                name="tag_unique_name_per_owner",
                violation_error_message="Já existe uma tag com este nome.",
            ),
        ]


class TagAlias(OwnedModel):
    """Grafia alternativa que resolve para uma tag (ex.: "supermercado" → Mercado)."""

    normalized_alias = models.CharField("apelido normalizado", max_length=60)
    tag = models.ForeignKey(Tag, on_delete=models.CASCADE, related_name="aliases")

    class Meta:
        verbose_name = "apelido de tag"
        verbose_name_plural = "apelidos de tags"
        ordering = ["normalized_alias"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "normalized_alias"],
                name="tagalias_unique_per_owner",
                violation_error_message="Este apelido já está em uso.",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.normalized_alias} → {self.tag}"
