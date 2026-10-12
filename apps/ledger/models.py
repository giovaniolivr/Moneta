import datetime

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.core.models import NormalizedNameModel, OwnedModel
from apps.core.money import MAX_CENTS, format_brl


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
    # Só cartão de crédito: base da fatura prevista e do pagamento na projeção de saldo.
    statement_closing_day = models.PositiveSmallIntegerField(
        "dia de fechamento", null=True, blank=True
    )
    payment_due_day = models.PositiveSmallIntegerField("dia de vencimento", null=True, blank=True)
    payment_account = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="paga pela conta",
    )

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

    @property
    def has_billing(self) -> bool:
        """Cartão com fechamento, vencimento e conta pagadora: entra na fatura prevista."""
        return bool(
            self.is_liability
            and self.statement_closing_day
            and self.payment_due_day
            and self.payment_account_id
        )

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        for field in ("statement_closing_day", "payment_due_day"):
            day = getattr(self, field)
            if day is not None and not 1 <= day <= 31:
                errors[field] = "Use um dia entre 1 e 31."
        payer = self.payment_account if self.payment_account_id else None
        if payer is not None:
            if payer.owner_id != self.owner_id:
                errors["payment_account"] = "Conta inválida."
            elif payer.is_liability or payer.pk == self.pk:
                errors["payment_account"] = "Escolha uma conta que não seja cartão."
        if not self.is_liability:
            self.statement_closing_day = self.payment_due_day = None
            self.payment_account = None
        if errors:
            raise ValidationError(errors)


class TransactionKind(models.TextChoices):
    INCOME = "income", "Receita"
    EXPENSE = "expense", "Despesa"
    TRANSFER = "transfer", "Transferência"


class TransactionStatus(models.TextChoices):
    """Realizada entra em saldos e análises; prevista é projeção (recorrência, parcela, conta
    futura) e só passa a contar quando o usuário a confirma."""

    POSTED = "posted", "Realizada"
    PLANNED = "planned", "Prevista"


class PaymentMethod(models.TextChoices):
    PIX = "pix", "Pix"
    DEBIT = "debit", "Débito"
    CREDIT = "credit", "Crédito"
    CASH = "cash", "Dinheiro"
    BOLETO = "boleto", "Boleto"
    OTHER = "other", "Outro"


class Transaction(OwnedModel):
    """Movimentação financeira realizada.

    `amount_cents` é sempre positivo; o efeito vem de `kind`:
    - receita: entra em `account`;
    - despesa: sai de `account`;
    - transferência: sai de `account` e entra em `destination_account`. Nunca é receita nem
      despesa nas análises consolidadas (ex.: aplicar na Caixinha, pagar a fatura do cartão).
    """

    kind = models.CharField("tipo", max_length=10, choices=TransactionKind.choices)
    status = models.CharField(
        "situação",
        max_length=10,
        choices=TransactionStatus.choices,
        default=TransactionStatus.POSTED,
    )
    amount_cents = models.BigIntegerField("valor (centavos)")
    date = models.DateField("data")
    account = models.ForeignKey(
        Account, on_delete=models.RESTRICT, related_name="transactions", verbose_name="conta"
    )
    destination_account = models.ForeignKey(
        Account,
        on_delete=models.RESTRICT,
        related_name="incoming_transfers",
        null=True,
        blank=True,
        verbose_name="conta de destino",
    )
    category = models.ForeignKey(
        "taxonomy.Category",
        on_delete=models.RESTRICT,
        related_name="transactions",
        null=True,
        blank=True,
        verbose_name="categoria",
    )
    tags = models.ManyToManyField(
        "taxonomy.Tag", related_name="transactions", blank=True, verbose_name="tags"
    )
    payment_method = models.CharField(
        "forma de pagamento", max_length=10, choices=PaymentMethod.choices, blank=True
    )
    description = models.CharField("descrição", max_length=140, blank=True)
    notes = models.TextField("observações", blank=True)
    # Origem de uma prevista gerada por recorrência (continua ligada depois de confirmada).
    recurrence = models.ForeignKey(
        "planning.Recurrence",
        on_delete=models.SET_NULL,
        related_name="transactions",
        null=True,
        blank=True,
        verbose_name="recorrência",
    )
    occurrence_date = models.DateField("data da ocorrência", null=True, blank=True)
    # Parcela de uma compra parcelada (número 1..N).
    installment_plan = models.ForeignKey(
        "planning.InstallmentPlan",
        on_delete=models.SET_NULL,
        related_name="transactions",
        null=True,
        blank=True,
        verbose_name="compra parcelada",
    )
    installment_number = models.PositiveSmallIntegerField("parcela", null=True, blank=True)

    class Meta:
        verbose_name = "movimentação"
        verbose_name_plural = "movimentações"
        ordering = ["-date", "-created_at"]
        indexes = [
            models.Index(fields=["owner", "date"]),
            models.Index(fields=["owner", "kind", "date"]),
            models.Index(fields=["owner", "category", "date"]),
            models.Index(fields=["owner", "status", "date"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount_cents__gt=0, amount_cents__lte=MAX_CENTS),
                name="transaction_amount_positive",
                violation_error_message="O valor deve ser maior que zero.",
            ),
            models.CheckConstraint(
                condition=models.Q(kind__in=TransactionKind.values),
                name="transaction_kind_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=TransactionStatus.values),
                name="transaction_status_valid",
            ),
            models.UniqueConstraint(
                fields=["recurrence", "occurrence_date"],
                condition=models.Q(recurrence__isnull=False),
                name="transaction_unique_occurrence",
                violation_error_message="Esta ocorrência da recorrência já existe.",
            ),
            models.UniqueConstraint(
                fields=["installment_plan", "installment_number"],
                condition=models.Q(installment_plan__isnull=False),
                name="transaction_unique_installment",
                violation_error_message="Esta parcela já existe.",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        kind=TransactionKind.TRANSFER,
                        destination_account__isnull=False,
                        category__isnull=True,
                    )
                    | (
                        ~models.Q(kind=TransactionKind.TRANSFER)
                        & models.Q(destination_account__isnull=True, category__isnull=False)
                    )
                ),
                name="transaction_transfer_shape",
                violation_error_message=(
                    "Transferências exigem conta de destino e não têm categoria; "
                    "receitas e despesas exigem categoria e não têm conta de destino."
                ),
            ),
            models.CheckConstraint(
                condition=~models.Q(account=models.F("destination_account")),
                name="transaction_transfer_distinct_accounts",
                violation_error_message="A conta de destino deve ser diferente da de origem.",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} {format_brl(self.amount_cents)} em {self.date:%d/%m/%Y}"

    @property
    def is_planned(self) -> bool:
        return self.status == TransactionStatus.PLANNED

    def clean(self) -> None:
        """Regras que envolvem objetos relacionados (o banco não consegue checar)."""
        errors: dict[str, str] = {}
        account = self.account if self.account_id else None
        destination = self.destination_account if self.destination_account_id else None
        category = self.category if self.category_id else None

        if account is not None and account.owner_id != self.owner_id:
            errors["account"] = "Conta inválida."
        if destination is not None and destination.owner_id != self.owner_id:
            errors["destination_account"] = "Conta de destino inválida."
        if category is not None and category.owner_id != self.owner_id:
            errors["category"] = "Categoria inválida."

        if self.kind == TransactionKind.TRANSFER:
            if destination is None:
                errors["destination_account"] = "Informe a conta de destino."
            elif destination.pk == self.account_id:
                errors["destination_account"] = "Escolha uma conta diferente da de origem."
            if category is not None:
                errors["category"] = "Transferências não têm categoria."
        else:
            if destination is not None:
                errors["destination_account"] = "Só transferências têm conta de destino."
            if category is None:
                errors["category"] = "Escolha uma categoria."
            elif "category" not in errors and category.kind != self.kind:
                kind_label = category.get_kind_display().lower()
                errors["category"] = f"A categoria “{category}” é de {kind_label}."

        if (
            self.date
            and self.date > timezone.localdate()
            and self.status != TransactionStatus.PLANNED
        ):
            errors["date"] = "Uma movimentação realizada não pode ter data futura."

        if errors:
            raise ValidationError(errors)
