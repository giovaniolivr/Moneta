import datetime
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import RestrictedError

from apps.taxonomy.models import Category, Tag
from apps.taxonomy.services import get_or_create_tags
from apps.users.models import User

from .models import Account, AccountKind, PaymentMethod, Transaction, TransactionKind

DEFAULT_ACCOUNT_NAME = "Principal"


def _validated_save(account: Account) -> Account:
    account.full_clean()
    account.save()
    return account


def create_account(
    user: User,
    *,
    name: str,
    kind: AccountKind | str,
    opening_balance_cents: int = 0,
    opening_date: datetime.date | None = None,
) -> Account:
    account = Account(owner=user, name=name, kind=kind, opening_balance_cents=opening_balance_cents)
    if opening_date is not None:
        account.opening_date = opening_date
    return _validated_save(account)


def update_account(
    account: Account,
    *,
    name: str | None = None,
    opening_balance_cents: int | None = None,
    opening_date: datetime.date | None = None,
) -> Account:
    """O tipo da conta não muda depois de criada: alteraria o significado do histórico."""
    if name is not None:
        account.name = name
    if opening_balance_cents is not None:
        account.opening_balance_cents = opening_balance_cents
    if opening_date is not None:
        account.opening_date = opening_date
    return _validated_save(account)


def set_account_archived(account: Account, *, archived: bool) -> Account:
    """Arquivar esconde a conta dos formulários, mas preserva o histórico e o saldo."""
    if archived:
        _ensure_another_active_account(account)
    account.is_archived = archived
    account.save(update_fields=["is_archived", "updated_at"])
    return account


@transaction.atomic
def ensure_default_account(user: User) -> Account:
    """Garante que o usuário tenha ao menos uma conta (de preferência ativa). Idempotente."""
    existing = Account.objects.for_user(user).order_by("is_archived", "created_at").first()
    if existing is not None:
        return existing
    return create_account(user, name=DEFAULT_ACCOUNT_NAME, kind=AccountKind.CHECKING)


# Movimentações ----------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class TransactionData:
    """Entrada completa de uma movimentação — usada por formulários, API e importação."""

    kind: TransactionKind | str
    amount_cents: int
    date: datetime.date
    account: Account
    category: Category | None = None
    destination_account: Account | None = None
    tags: Sequence[str] = ()
    payment_method: PaymentMethod | str = ""
    description: str = ""
    notes: str = ""


def _reject_newly_archived(tx: Transaction, previous_ids: set[uuid.UUID]) -> None:
    """Contas/categorias arquivadas não recebem movimentações novas, mas as antigas continuam
    editáveis sem precisar trocá-las."""
    errors = {
        field: f"“{obj}” está arquivada."
        for field, obj in (
            ("account", tx.account),
            ("destination_account", tx.destination_account),
            ("category", tx.category),
        )
        if obj is not None and obj.is_archived and obj.pk not in previous_ids
    }
    if errors:
        raise ValidationError(errors)


def _save(tx: Transaction, data: TransactionData, previous_ids: set[uuid.UUID]) -> Transaction:
    tx.kind = data.kind
    tx.amount_cents = data.amount_cents
    tx.date = data.date
    tx.account = data.account
    tx.destination_account = data.destination_account
    tx.category = data.category
    tx.payment_method = data.payment_method
    tx.description = data.description.strip()
    tx.notes = data.notes.strip()
    _reject_newly_archived(tx, previous_ids)
    tx.full_clean()
    tx.save()
    tx.tags.set(get_or_create_tags(tx.owner, data.tags))
    return tx


@transaction.atomic
def create_transaction(user: User, data: TransactionData) -> Transaction:
    return _save(Transaction(owner=user), data, previous_ids=set())


class BatchError(Exception):
    """Uma ou mais linhas do lote são inválidas; nada foi gravado."""

    def __init__(self, errors: dict[int, ValidationError]) -> None:
        super().__init__(f"{len(errors)} linha(s) com erro")
        self.errors = errors


def create_transactions(user: User, items: Sequence[TransactionData]) -> list[Transaction]:
    """Grava um lote de movimentações: tudo ou nada.

    Todas as linhas são validadas (mesmo depois do primeiro erro) para que o usuário veja
    todos os problemas de uma vez; havendo qualquer erro, nada é gravado (rollback).
    """
    created: list[Transaction] = []
    errors: dict[int, ValidationError] = {}
    with transaction.atomic():
        for index, data in enumerate(items):
            try:
                with transaction.atomic():  # savepoint: um erro não invalida a transação externa
                    created.append(create_transaction(user, data))
            except ValidationError as error:
                errors[index] = error
        if errors:
            transaction.set_rollback(True)
    if errors:
        raise BatchError(errors)
    return created


@transaction.atomic
def update_transaction(tx: Transaction, data: TransactionData) -> Transaction:
    """Substitui todos os campos (inclusive as tags) pelos de `data`."""
    previous_ids = {
        pk for pk in (tx.account_id, tx.destination_account_id, tx.category_id) if pk is not None
    }
    return _save(tx, data, previous_ids)


def delete_transaction(tx: Transaction) -> None:
    tx.delete()


def transaction_snapshot(tx: Transaction) -> dict[str, Any]:
    """Cópia serializável (JSON) de uma movimentação, para desfazer a exclusão."""
    return {
        "id": str(tx.pk),
        "kind": tx.kind,
        "amount_cents": tx.amount_cents,
        "date": tx.date.isoformat(),
        "account": str(tx.account_id),
        "destination_account": str(tx.destination_account_id or ""),
        "category": str(tx.category_id or ""),
        "tags": [str(pk) for pk in tx.tags.values_list("pk", flat=True)],
        "payment_method": tx.payment_method,
        "description": tx.description,
        "notes": tx.notes,
    }


@transaction.atomic
def restore_transaction(user: User, snapshot: dict[str, Any]) -> Transaction:
    """Recria uma movimentação excluída, com o mesmo id. Contas/categorias precisam ainda
    existir; tags removidas nesse meio-tempo são ignoradas."""
    if Transaction.objects.filter(pk=snapshot["id"]).exists():
        raise ValidationError("Esta movimentação já foi restaurada.")

    def owned(model: type[Account] | type[Category], key: str, label: str) -> Any:
        if not snapshot[key]:
            return None
        obj = model.objects.for_user(user).filter(pk=snapshot[key]).first()
        if obj is None:
            raise ValidationError(f"Não foi possível desfazer: a {label} não existe mais.")
        return obj

    tx = Transaction(
        id=uuid.UUID(snapshot["id"]),
        owner=user,
        kind=snapshot["kind"],
        amount_cents=snapshot["amount_cents"],
        date=datetime.date.fromisoformat(snapshot["date"]),
        account=owned(Account, "account", "conta"),
        destination_account=owned(Account, "destination_account", "conta de destino"),
        category=owned(Category, "category", "categoria"),
        payment_method=snapshot["payment_method"],
        description=snapshot["description"],
        notes=snapshot["notes"],
    )
    tx.full_clean()
    tx.save()
    tx.tags.set(Tag.objects.for_user(user).filter(pk__in=snapshot["tags"]))
    return tx


def delete_account(account: Account) -> None:
    """Só contas sem movimentações podem ser excluídas; as demais são arquivadas."""
    _ensure_another_active_account(account)
    try:
        account.delete()
    except RestrictedError as exc:
        raise ValidationError(
            "Esta conta tem movimentações. Arquive-a para escondê-la sem perder o histórico."
        ) from exc


def _ensure_another_active_account(account: Account) -> None:
    if account.is_archived:
        return
    others = Account.objects.filter(owner_id=account.owner_id, is_archived=False).exclude(
        pk=account.pk
    )
    if not others.exists():
        raise ValidationError("Mantenha ao menos uma conta ativa para registrar movimentações.")
