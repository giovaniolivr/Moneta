import datetime
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.taxonomy.models import Category
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


@transaction.atomic
def update_transaction(tx: Transaction, data: TransactionData) -> Transaction:
    """Substitui todos os campos (inclusive as tags) pelos de `data`."""
    previous_ids = {
        pk for pk in (tx.account_id, tx.destination_account_id, tx.category_id) if pk is not None
    }
    return _save(tx, data, previous_ids)


def delete_transaction(tx: Transaction) -> None:
    tx.delete()
