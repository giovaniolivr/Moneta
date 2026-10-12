import datetime
from typing import Any

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.core.models import OwnedModel
from apps.core.names import InvalidNameError, clean_display_name, normalize_name


class AccountKind(models.TextChoices):
    CHECKING = "checking", "Conta corrente"
    SAVINGS = "savings", "Poupança"
    CASH = "cash", "Dinheiro"
    CREDIT_CARD = "credit_card", "Cartão de crédito"
    INVESTMENT = "investment", "Investimento"


def _today() -> datetime.date:
    return timezone.localdate()


class Account(OwnedModel):
    """Origem ou destino do dinheiro.

    Cartão de crédito é uma conta (passivo): compras são despesas nela e o pagamento da fatura é
    uma transferência da conta corrente para o cartão — assim a despesa nunca é contada duas vezes.
    """

    name = models.CharField("nome", max_length=60)
    normalized_name = models.CharField(max_length=60, editable=False, blank=True)  # calculado
    kind = models.CharField("tipo", max_length=20, choices=AccountKind.choices)
    currency = models.CharField("moeda", max_length=3, default="BRL", editable=False)
    # Pode ser negativo (fatura em aberto, cheque especial). Em centavos.
    opening_balance_cents = models.BigIntegerField("saldo inicial (centavos)", default=0)
    opening_date = models.DateField("data do saldo inicial", default=_today)
    is_archived = models.BooleanField("arquivada", default=False)

    class Meta:
        verbose_name = "conta"
        verbose_name_plural = "contas"
        ordering = ["is_archived", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "normalized_name"],
                name="account_unique_name_per_owner",
                violation_error_message="Já existe uma conta com este nome.",
            ),
        ]

    def __str__(self) -> str:
        return self.name

    @property
    def is_liability(self) -> bool:
        return self.kind == AccountKind.CREDIT_CARD

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
