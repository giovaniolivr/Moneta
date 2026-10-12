import datetime

from django.db import transaction

from apps.users.models import User

from .models import Account, AccountKind

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
