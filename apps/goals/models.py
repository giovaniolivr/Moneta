from django.db import models

from apps.core.models import OwnedModel
from apps.core.money import MAX_CENTS
from apps.ledger.models import Account


class Goal(OwnedModel):
    """Meta de valor medida pelo saldo de uma conta (ex.: reserva de emergência na Caixinha).

    Uma meta por conta: duas metas na mesma conta contariam o mesmo dinheiro duas vezes.
    """

    name = models.CharField("nome", max_length=80)
    target_cents = models.BigIntegerField("valor-alvo (centavos)")
    account = models.ForeignKey(
        Account, on_delete=models.RESTRICT, related_name="goals", verbose_name="conta"
    )
    deadline = models.DateField("prazo", null=True, blank=True)

    class Meta:
        verbose_name = "meta"
        verbose_name_plural = "metas"
        ordering = ["deadline", "name"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(target_cents__gt=0, target_cents__lte=MAX_CENTS),
                name="goal_target_positive",
                violation_error_message="O valor-alvo deve ser maior que zero.",
            ),
            models.UniqueConstraint(
                fields=["owner", "account"],
                name="goal_unique_account",
                violation_error_message="Esta conta já tem uma meta.",
            ),
        ]

    def __str__(self) -> str:
        return self.name
