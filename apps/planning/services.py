"""Recorrências: cadastro e geração das movimentações previstas.

Geração (`materialize`) é preguiçosa e idempotente: roda ao abrir as telas que mostram previstas
e só cria o que falta até `hoje + HORIZON_DAYS`. Não há tarefa em segundo plano.
"""

import dataclasses
import datetime
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.ledger.models import Transaction, TransactionKind, TransactionStatus
from apps.ledger.services import TransactionData
from apps.taxonomy.models import Category, CategoryKind
from apps.taxonomy.services import get_or_create_tags
from apps.users.models import User

from .domain.installments import due_dates as installment_dates
from .domain.installments import label as installment_label
from .domain.installments import split as split_installments
from .domain.schedule import Frequency, occurrences
from .models import Budget, InstallmentPlan, Recurrence

HORIZON_DAYS = 92  # ~3 meses de previstas à frente
SCHEDULE_FIELDS = ("frequency", "interval", "start_date", "end_date")
_EDITABLE_FIELDS = (
    "kind",
    "amount_cents",
    "account",
    "destination_account",
    "category",
    "payment_method",
    "description",
    "notes",
    "updated_at",
)


@dataclasses.dataclass(frozen=True)
class RecurrenceData:
    entry: TransactionData  # tipo, valor, conta, categoria, tags... (`entry.date` é ignorada)
    frequency: Frequency | str
    interval: int
    start_date: datetime.date
    end_date: datetime.date | None = None


def _apply(rec: Recurrence, data: RecurrenceData) -> None:
    e = data.entry
    rec.kind = e.kind
    rec.amount_cents = e.amount_cents
    rec.account = e.account
    rec.destination_account = e.destination_account if e.kind == TransactionKind.TRANSFER else None
    rec.category = None if e.kind == TransactionKind.TRANSFER else e.category
    rec.payment_method = e.payment_method
    rec.description = e.description.strip()
    rec.notes = e.notes.strip()
    rec.frequency = data.frequency
    rec.interval = data.interval
    rec.start_date = data.start_date
    rec.end_date = data.end_date


def _occurrence(rec: Recurrence, day: datetime.date) -> Transaction:
    return Transaction(
        owner_id=rec.owner_id,
        kind=rec.kind,
        status=TransactionStatus.PLANNED,
        amount_cents=rec.amount_cents,
        date=day,
        account=rec.account,
        destination_account=rec.destination_account,
        category=rec.category,
        payment_method=rec.payment_method,
        description=rec.description,
        notes=rec.notes,
        recurrence=rec,
        occurrence_date=day,
    )


def _validate(rec: Recurrence) -> None:
    """Regras da recorrência + as de uma movimentação (mesmo dono, tipo da categoria...)."""
    errors: dict[str, Any] = {}
    try:
        rec.full_clean()
    except ValidationError as exc:
        errors.update(exc.message_dict)
    try:
        _occurrence(rec, rec.start_date).full_clean(
            exclude=["recurrence", "occurrence_date", "date"], validate_constraints=False
        )
    except ValidationError as exc:
        for field, messages in exc.message_dict.items():
            errors.setdefault(field, messages)
    if errors:
        raise ValidationError(errors)


def _cursor_for_new(rec: Recurrence, today: datetime.date) -> datetime.date:
    """Previstas começam hoje (o passado o usuário registra como realizado)."""
    return max(rec.start_date, today) - datetime.timedelta(days=1)


def _future_planned(rec: Recurrence, today: datetime.date) -> Any:
    return rec.transactions.filter(status=TransactionStatus.PLANNED, date__gte=today)


@transaction.atomic
def create_recurrence(user: User, data: RecurrenceData, *, today: datetime.date) -> Recurrence:
    rec = Recurrence(owner=user)
    _apply(rec, data)
    _validate(rec)
    rec.generated_through = _cursor_for_new(rec, today)
    rec.save()
    rec.tags.set(get_or_create_tags(user, data.entry.tags))
    materialize(user, today=today)
    rec.refresh_from_db(fields=["generated_through"])
    return rec


@transaction.atomic
def update_recurrence(rec: Recurrence, data: RecurrenceData, *, today: datetime.date) -> Recurrence:
    """Atualiza a recorrência e as próximas previstas ainda não confirmadas.

    Mudou só valor/conta/categoria...: as previstas futuras são ajustadas no lugar (as que o
    usuário pulou continuam puladas). Mudou o calendário: as previstas futuras são refeitas.
    """
    before = {f: getattr(rec, f) for f in SCHEDULE_FIELDS}
    _apply(rec, data)
    _validate(rec)
    tags = get_or_create_tags(rec.owner, data.entry.tags)
    rec.tags.set(tags)
    if any(getattr(rec, f) != v for f, v in before.items()):
        _future_planned(rec, today).delete()
        rec.generated_through = _cursor_for_new(rec, today)
        rec.save()
        materialize(rec.owner, today=today)
        return rec
    # O cursor nunca volta: senão as ocorrências puladas seriam recriadas.
    rec.save(update_fields=[f for f in _EDITABLE_FIELDS])
    for tx in _future_planned(rec, today):
        fresh = _occurrence(rec, tx.date)
        for field in (
            "kind",
            "amount_cents",
            "account",
            "destination_account",
            "category",
            "payment_method",
            "description",
            "notes",
        ):
            setattr(tx, field, getattr(fresh, field))
        tx.full_clean()
        tx.save()
        tx.tags.set(tags)
    return rec


@transaction.atomic
def set_recurrence_active(rec: Recurrence, *, active: bool, today: datetime.date) -> Recurrence:
    """Pausar remove as próximas previstas; retomar volta a gerar a partir de hoje."""
    if rec.is_active == active:
        return rec
    rec.is_active = active
    if active:
        rec.generated_through = _cursor_for_new(rec, today)
        rec.save()
        materialize(rec.owner, today=today)
    else:
        _future_planned(rec, today).delete()
        rec.save()
    return rec


@transaction.atomic
def delete_recurrence(rec: Recurrence) -> None:
    """Remove a recorrência e todas as previstas dela; as já confirmadas ficam no histórico."""
    rec.transactions.filter(status=TransactionStatus.PLANNED).delete()
    rec.delete()


@transaction.atomic
def materialize(user: User, *, today: datetime.date) -> int:
    """Cria as previstas que faltam até `today + HORIZON_DAYS`. Retorna quantas criou."""
    horizon = today + datetime.timedelta(days=HORIZON_DAYS)
    pending = (
        Recurrence.objects.for_user(user)
        .filter(is_active=True)
        .exclude(generated_through__gte=horizon)
    )
    created = 0
    for rec in pending.select_related("account", "destination_account", "category"):
        tags = list(rec.tags.all())
        for day in occurrences(
            rec.start_date,
            Frequency(rec.frequency),
            rec.interval,
            after=rec.generated_through,
            through=horizon,
            end=rec.end_date,
        ):
            tx = _occurrence(rec, day)
            tx.full_clean(validate_constraints=False)
            tx.save()
            tx.tags.set(tags)
            created += 1
        rec.generated_through = horizon
        rec.save(update_fields=["generated_through", "updated_at"])
    return created


# Compras parceladas ------------------------------------------------------------


def _installment_label(plan: InstallmentPlan, number: int) -> str:
    return installment_label(plan.description, number, plan.count)


@transaction.atomic
def create_installment_plan(
    user: User, data: TransactionData, *, count: int, today: datetime.date
) -> InstallmentPlan:
    """Despesa parcelada: o valor de `data` é o TOTAL; gera uma movimentação por parcela.

    Parcelas com data até hoje nascem realizadas (a compra aconteceu); as futuras, previstas.
    """
    if data.kind != TransactionKind.EXPENSE:
        raise ValidationError({"kind": "Só despesas podem ser parceladas."})
    try:
        amounts = split_installments(data.amount_cents, count)
    except ValueError as exc:
        raise ValidationError({"installments": str(exc).capitalize() + "."}) from None
    category = data.category
    plan = InstallmentPlan(
        owner=user,
        description=(data.description.strip() or (category.name if category else "Compra"))[:120],
        total_cents=data.amount_cents,
        count=count,
        purchase_date=data.date,
        account=data.account,
        category=category,
        payment_method=data.payment_method,
        notes=data.notes.strip(),
    )
    plan.full_clean()
    plan.save()
    tags = get_or_create_tags(user, data.tags)
    plan.tags.set(tags)
    for number, (amount, day) in enumerate(
        zip(amounts, installment_dates(data.date, count), strict=True), start=1
    ):
        tx = Transaction(
            owner=user,
            kind=TransactionKind.EXPENSE,
            status=TransactionStatus.POSTED if day <= today else TransactionStatus.PLANNED,
            amount_cents=amount,
            date=day,
            account=plan.account,
            category=plan.category,
            payment_method=plan.payment_method,
            description=_installment_label(plan, number),
            notes=plan.notes,
            installment_plan=plan,
            installment_number=number,
        )
        tx.full_clean()
        tx.save()
        tx.tags.set(tags)
    return plan


@transaction.atomic
def update_installment_plan(
    plan: InstallmentPlan,
    *,
    description: str,
    category: Any,
    tag_names: list[str],
    notes: str,
) -> InstallmentPlan:
    """Descrição, categoria, tags e observações valem para todas as parcelas (é a mesma
    compra). Valor, parcelas e data não mudam: para isso, exclua e registre de novo."""
    plan.description = description.strip()[:120]
    plan.category = category
    plan.notes = notes.strip()
    plan.full_clean()
    plan.save()
    tags = get_or_create_tags(plan.owner, tag_names)
    plan.tags.set(tags)
    for tx in plan.transactions.all():
        tx.description = _installment_label(plan, tx.installment_number or 0)
        tx.category = plan.category
        tx.notes = plan.notes
        tx.full_clean()
        tx.save(update_fields=["description", "category", "notes", "updated_at"])
        tx.tags.set(tags)
    return plan


@transaction.atomic
def delete_installment_plan(plan: InstallmentPlan) -> None:
    """Remove as parcelas previstas; as já realizadas ficam no histórico."""
    plan.transactions.filter(status=TransactionStatus.PLANNED).delete()
    plan.delete()


# Orçamentos --------------------------------------------------------------------


def _clean_budget(budget: Budget) -> Budget:
    category = budget.category if budget.category_id else None
    if category is not None:
        if category.owner_id != budget.owner_id:
            raise ValidationError({"category": "Categoria inválida."})
        if category.kind != CategoryKind.EXPENSE:
            raise ValidationError({"category": "Orçamento é só para categorias de despesa."})
    budget.full_clean()
    budget.save()
    return budget


def create_budget(user: User, *, category: Category | None, amount_cents: int) -> Budget:
    return _clean_budget(Budget(owner=user, category=category, amount_cents=amount_cents))


def update_budget(budget: Budget, *, amount_cents: int) -> Budget:
    budget.amount_cents = amount_cents
    return _clean_budget(budget)


def delete_budget(budget: Budget) -> None:
    budget.delete()
