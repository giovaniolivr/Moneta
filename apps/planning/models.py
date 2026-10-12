import datetime

from django.db import models

from apps.core.models import OwnedModel
from apps.core.money import MAX_CENTS
from apps.ledger.models import Account, PaymentMethod, TransactionKind

from .domain.schedule import Frequency, describe, monthly_equivalent, next_occurrence


class Recurrence(OwnedModel):
    """Movimentação que se repete (aluguel, salário, assinatura).

    Gera movimentações **previstas** num horizonte à frente (`services.materialize`); elas só
    contam depois que o usuário as confirma. `generated_through` é o cursor da geração: uma
    prevista excluída ("pular") não é recriada.
    """

    kind = models.CharField("tipo", max_length=10, choices=TransactionKind.choices)
    amount_cents = models.BigIntegerField("valor (centavos)")
    account = models.ForeignKey(
        Account, on_delete=models.RESTRICT, related_name="recurrences", verbose_name="conta"
    )
    destination_account = models.ForeignKey(
        Account,
        on_delete=models.RESTRICT,
        related_name="incoming_recurrences",
        null=True,
        blank=True,
        verbose_name="conta de destino",
    )
    category = models.ForeignKey(
        "taxonomy.Category",
        on_delete=models.RESTRICT,
        related_name="recurrences",
        null=True,
        blank=True,
        verbose_name="categoria",
    )
    tags = models.ManyToManyField(
        "taxonomy.Tag", related_name="recurrences", blank=True, verbose_name="tags"
    )
    payment_method = models.CharField(
        "forma de pagamento", max_length=10, choices=PaymentMethod.choices, blank=True
    )
    description = models.CharField("descrição", max_length=140, blank=True)
    notes = models.TextField("observações", blank=True)

    frequency = models.CharField(
        "frequência",
        max_length=10,
        choices=[
            (Frequency.WEEKLY, "Semanal"),
            (Frequency.MONTHLY, "Mensal"),
            (Frequency.YEARLY, "Anual"),
        ],
        default=Frequency.MONTHLY,
    )
    interval = models.PositiveSmallIntegerField("a cada", default=1)
    start_date = models.DateField("primeira data")
    end_date = models.DateField("até", null=True, blank=True)
    is_active = models.BooleanField("ativa", default=True)
    generated_through = models.DateField("previstas geradas até", null=True, blank=True)

    class Meta:
        verbose_name = "recorrência"
        verbose_name_plural = "recorrências"
        ordering = ["-is_active", "kind", "description"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount_cents__gt=0, amount_cents__lte=MAX_CENTS),
                name="recurrence_amount_positive",
                violation_error_message="O valor deve ser maior que zero.",
            ),
            models.CheckConstraint(
                condition=models.Q(interval__gte=1, interval__lte=12),
                name="recurrence_interval_range",
                violation_error_message="O intervalo deve ficar entre 1 e 12.",
            ),
            models.CheckConstraint(
                condition=models.Q(end_date__isnull=True)
                | models.Q(end_date__gte=models.F("start_date")),
                name="recurrence_end_after_start",
                violation_error_message="A data final deve ser depois da primeira data.",
            ),
        ]

    def __str__(self) -> str:
        return self.label

    @property
    def label(self) -> str:
        if self.description:
            return self.description
        if self.category_id and self.category:
            return self.category.name
        return "Transferência"

    @property
    def schedule(self) -> str:
        return describe(self.start_date, Frequency(self.frequency), self.interval)

    @property
    def monthly_cents(self) -> int:
        return monthly_equivalent(self.amount_cents, Frequency(self.frequency), self.interval)

    def next_date(self, today: datetime.date) -> datetime.date | None:
        if not self.is_active:
            return None
        return next_occurrence(
            self.start_date,
            Frequency(self.frequency),
            self.interval,
            on_or_after=today,
            end=self.end_date,
        )
