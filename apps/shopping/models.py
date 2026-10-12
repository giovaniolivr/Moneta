from django.db import models

from apps.core.models import OwnedModel
from apps.core.money import MAX_CENTS


class WishItem(OwnedModel):
    """Algo que o usuário quer comprar e cujo preço acompanha."""

    name = models.CharField("nome", max_length=120)
    target_cents = models.BigIntegerField("preço-alvo (centavos)", null=True, blank=True)
    notes = models.TextField("observações", blank=True)
    is_done = models.BooleanField("comprado/desistiu", default=False)

    class Meta:
        verbose_name = "item desejado"
        verbose_name_plural = "lista de desejos"
        ordering = ["is_done", "name"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(target_cents__isnull=True)
                | models.Q(target_cents__gt=0, target_cents__lte=MAX_CENTS),
                name="wishitem_target_positive",
                violation_error_message="O preço-alvo deve ser maior que zero.",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class PriceSource(models.TextChoices):
    MANUAL = "manual", "Registrado por você"
    MERCADO_LIVRE = "mercadolivre", "API do Mercado Livre"


class PriceObservation(OwnedModel):
    """Um preço visto numa loja num dia (manual ou de um coletor oficial)."""

    item = models.ForeignKey(
        WishItem, on_delete=models.CASCADE, related_name="observations", verbose_name="item"
    )
    store = models.CharField("loja", max_length=80)
    url = models.URLField("link", max_length=500, blank=True)
    price_cents = models.BigIntegerField("preço (centavos)")
    shipping_cents = models.BigIntegerField("frete (centavos)", default=0)
    installments = models.PositiveSmallIntegerField("parcelas sem juros", null=True, blank=True)
    observed_on = models.DateField("data")
    source = models.CharField(
        "origem", max_length=20, choices=PriceSource.choices, default=PriceSource.MANUAL
    )

    class Meta:
        verbose_name = "preço observado"
        verbose_name_plural = "preços observados"
        ordering = ["-observed_on", "-created_at"]
        indexes = [models.Index(fields=["owner", "item", "observed_on"])]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(price_cents__gt=0, price_cents__lte=MAX_CENTS),
                name="priceobservation_price_positive",
                violation_error_message="O preço deve ser maior que zero.",
            ),
            models.CheckConstraint(
                condition=models.Q(shipping_cents__gte=0),
                name="priceobservation_shipping_not_negative",
                violation_error_message="O frete não pode ser negativo.",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.store}: {self.price_cents} em {self.observed_on:%d/%m/%Y}"

    @property
    def total_cents(self) -> int:
        return self.price_cents + self.shipping_cents
