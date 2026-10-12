import datetime

from django.db import models
from django.utils import timezone

from apps.core.models import NormalizedNameModel, OwnedModel


class AccountKind(models.TextChoices):
    CHECKING = "checking", "Conta corrente"
    SAVINGS = "savings", "Poupança"
    CASH = "cash", "Dinheiro"
    CREDIT_CARD = "credit_card", "Cartão de crédito"
    INVESTMENT = "investment", "Investimento"


def _today() -> datetime.date:
    return timezone.localdate()


class Account(NormalizedNameModel, OwnedModel):
    """Origem ou destino do dinheiro.

    Cartão de crédito é uma conta (passivo): compras são despesas nela e o pagamento da fatura é
    uma transferência da conta corrente para o cartão — assim a despesa nunca é contada duas vezes.
    """

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

    @property
    def is_liability(self) -> bool:
        return self.kind == AccountKind.CREDIT_CARD
