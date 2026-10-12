import datetime
from dataclasses import replace
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import RestrictedError
from django.utils import timezone

from apps.core.money import MAX_CENTS
from apps.ledger.models import Account, AccountKind, PaymentMethod, Transaction, TransactionKind
from apps.ledger.selectors import account_balances, list_transactions
from apps.ledger.services import (
    TransactionData,
    create_account,
    create_transaction,
    delete_transaction,
    ensure_default_account,
    set_account_archived,
    update_transaction,
)
from apps.taxonomy.models import Category, Tag
from apps.taxonomy.services import (
    create_category,
    get_or_create_tag,
    merge_tags,
    set_category_archived,
)

pytestmark = pytest.mark.django_db

TODAY = datetime.date(2026, 10, 10)


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(timezone, "localdate", lambda *a, **k: TODAY)


@pytest.fixture
def checking(user: Any) -> Account:
    account = ensure_default_account(user)
    account.opening_date = datetime.date(2026, 1, 1)
    account.save()
    return account


@pytest.fixture
def savings(user: Any) -> Account:
    return create_account(
        user, name="Caixinha", kind=AccountKind.SAVINGS, opening_date=datetime.date(2026, 1, 1)
    )


@pytest.fixture
def food(user: Any) -> Category:
    return Category.objects.for_user(user).get(name="Alimentação")


@pytest.fixture
def salary(user: Any) -> Category:
    return Category.objects.for_user(user).get(name="Salário")


def expense(account: Account, category: Category, cents: int, **kw: Any) -> TransactionData:
    return TransactionData(
        kind=TransactionKind.EXPENSE,
        amount_cents=cents,
        date=kw.pop("date", TODAY),
        account=account,
        category=category,
        **kw,
    )


def transfer(source: Account, dest: Account, cents: int, **kw: Any) -> TransactionData:
    return TransactionData(
        kind=TransactionKind.TRANSFER,
        amount_cents=cents,
        date=kw.pop("date", TODAY),
        account=source,
        destination_account=dest,
        **kw,
    )


class TestCreate:
    def test_expense_with_tags(self, user: Any, checking: Account, food: Category) -> None:
        tx = create_transaction(
            user,
            expense(
                checking,
                food,
                4500,
                tags=["Restaurante", "restaurante", " Almoço "],
                payment_method=PaymentMethod.PIX,
                description="  Almoço  ",
            ),
        )
        assert tx.owner == user
        assert tx.description == "Almoço"
        assert sorted(t.name for t in tx.tags.all()) == ["Almoço", "Restaurante"]
        assert str(tx) == "Despesa R$\xa045,00 em 10/10/2026"

    def test_transfer(self, user: Any, checking: Account, savings: Account) -> None:
        tx = create_transaction(user, transfer(checking, savings, 10_000))
        assert tx.category is None
        assert tx.destination_account == savings

    @pytest.mark.parametrize("cents", [0, -1, MAX_CENTS + 1])
    def test_invalid_amount(self, user: Any, checking: Account, food: Category, cents: int) -> None:
        with pytest.raises(ValidationError):
            create_transaction(user, expense(checking, food, cents))

    def test_category_kind_must_match(self, user: Any, checking: Account, salary: Category) -> None:
        with pytest.raises(ValidationError) as exc:
            create_transaction(user, expense(checking, salary, 100))
        assert "category" in exc.value.message_dict

    def test_income_and_expense_require_category(self, user: Any, checking: Account) -> None:
        data = TransactionData(
            kind=TransactionKind.INCOME, amount_cents=100, date=TODAY, account=checking
        )
        with pytest.raises(ValidationError) as exc:
            create_transaction(user, data)
        assert "category" in exc.value.message_dict

    def test_transfer_rules(
        self, user: Any, checking: Account, savings: Account, food: Category
    ) -> None:
        with pytest.raises(ValidationError) as exc:
            create_transaction(user, transfer(checking, checking, 100))
        assert "destination_account" in exc.value.message_dict

        with pytest.raises(ValidationError) as exc:
            create_transaction(user, transfer(checking, savings, 100, category=food))
        assert "category" in exc.value.message_dict

        no_destination = replace(transfer(checking, savings, 100), destination_account=None)
        with pytest.raises(ValidationError):
            create_transaction(user, no_destination)

    def test_expense_cannot_have_destination(
        self, user: Any, checking: Account, savings: Account, food: Category
    ) -> None:
        with pytest.raises(ValidationError) as exc:
            create_transaction(user, expense(checking, food, 100, destination_account=savings))
        assert "destination_account" in exc.value.message_dict

    def test_future_date_rejected(self, user: Any, checking: Account, food: Category) -> None:
        tomorrow = TODAY + datetime.timedelta(days=1)
        with pytest.raises(ValidationError) as exc:
            create_transaction(user, expense(checking, food, 100, date=tomorrow))
        assert "date" in exc.value.message_dict

    def test_invalid_payment_method(self, user: Any, checking: Account, food: Category) -> None:
        with pytest.raises(ValidationError):
            create_transaction(user, expense(checking, food, 100, payment_method="bitcoin"))

    def test_archived_account_or_category_rejected(
        self, user: Any, checking: Account, food: Category
    ) -> None:
        set_category_archived(food, archived=True)
        with pytest.raises(ValidationError, match="arquivada"):
            create_transaction(user, expense(checking, food, 100))
        set_category_archived(food, archived=False)
        create_account(user, name="Outra", kind=AccountKind.CASH)
        set_account_archived(checking, archived=True)
        with pytest.raises(ValidationError, match="arquivada"):
            create_transaction(user, expense(checking, food, 100))


class TestDatabaseConstraints:
    """Mesmo contornando os services, o banco recusa dados financeiros inconsistentes."""

    def _raw(self, **fields: Any) -> None:
        with pytest.raises(IntegrityError), transaction.atomic():
            Transaction.objects.create(date=TODAY, **fields)

    def test_amount_must_be_positive(self, user: Any, checking: Account, food: Category) -> None:
        self._raw(owner=user, kind="expense", amount_cents=0, account=checking, category=food)

    def test_transfer_shape(
        self, user: Any, checking: Account, savings: Account, food: Category
    ) -> None:
        self._raw(owner=user, kind="transfer", amount_cents=1, account=checking, category=food)
        self._raw(owner=user, kind="expense", amount_cents=1, account=checking)
        self._raw(
            owner=user,
            kind="transfer",
            amount_cents=1,
            account=checking,
            destination_account=checking,
        )

    def test_kind_must_be_valid(self, user: Any, checking: Account, food: Category) -> None:
        self._raw(owner=user, kind="refund", amount_cents=1, account=checking, category=food)


class TestUpdateAndDelete:
    def test_update_replaces_fields_and_tags(
        self, user: Any, checking: Account, food: Category
    ) -> None:
        tx = create_transaction(user, expense(checking, food, 100, tags=["Mercado"]))
        update_transaction(tx, expense(checking, food, 250, tags=["Feira"]))
        tx.refresh_from_db()
        assert tx.amount_cents == 250
        assert [t.name for t in tx.tags.all()] == ["Feira"]

    def test_can_edit_transaction_on_now_archived_account(
        self, user: Any, checking: Account, food: Category
    ) -> None:
        tx = create_transaction(user, expense(checking, food, 100))
        create_account(user, name="Outra", kind=AccountKind.CASH)
        set_account_archived(checking, archived=True)
        update_transaction(tx, expense(checking, food, 200))
        assert tx.amount_cents == 200

    def test_delete(self, user: Any, checking: Account, food: Category) -> None:
        tx = create_transaction(user, expense(checking, food, 100, tags=["Mercado"]))
        delete_transaction(tx)
        assert not list_transactions(user).exists()
        assert Tag.objects.for_user(user).filter(name="Mercado").exists()  # tag permanece

    def test_category_and_account_with_history_are_protected(
        self, user: Any, checking: Account, food: Category
    ) -> None:
        create_transaction(user, expense(checking, food, 100))
        with pytest.raises(RestrictedError):
            food.delete()
        with pytest.raises(RestrictedError):
            checking.delete()


class TestIsolation:
    def test_cannot_use_other_users_account_or_category(
        self, user: Any, other_user: Any, checking: Account, food: Category
    ) -> None:
        their_account = ensure_default_account(other_user)
        their_food = Category.objects.for_user(other_user).get(name="Alimentação")
        with pytest.raises(ValidationError) as exc:
            create_transaction(user, expense(their_account, food, 100))
        assert "account" in exc.value.message_dict
        with pytest.raises(ValidationError) as exc:
            create_transaction(user, expense(checking, their_food, 100))
        assert "category" in exc.value.message_dict

    def test_cannot_transfer_to_other_users_account(
        self, user: Any, other_user: Any, checking: Account
    ) -> None:
        with pytest.raises(ValidationError) as exc:
            create_transaction(user, transfer(checking, ensure_default_account(other_user), 100))
        assert "destination_account" in exc.value.message_dict

    def test_listing_is_scoped(
        self, user: Any, other_user: Any, checking: Account, food: Category
    ) -> None:
        create_transaction(user, expense(checking, food, 100))
        assert list_transactions(user).count() == 1
        assert list_transactions(other_user).count() == 0
        assert account_balances(other_user) == {ensure_default_account(other_user).pk: 0}


class TestBalances:
    def test_income_expense_and_transfer(
        self, user: Any, checking: Account, savings: Account, food: Category, salary: Category
    ) -> None:
        checking.opening_balance_cents = 100_000
        checking.save()
        create_transaction(
            user,
            TransactionData(
                kind=TransactionKind.INCOME,
                amount_cents=500_000,
                date=TODAY,
                account=checking,
                category=salary,
            ),
        )
        create_transaction(user, expense(checking, food, 20_000))
        create_transaction(user, transfer(checking, savings, 300_000))
        balances = account_balances(user)
        assert balances[checking.pk] == 100_000 + 500_000 - 20_000 - 300_000
        assert balances[savings.pk] == 300_000
        # Transferência não cria nem destrói dinheiro.
        assert sum(balances.values()) == 100_000 + 500_000 - 20_000

    def test_credit_card_purchase_and_bill_payment(
        self, user: Any, checking: Account, food: Category
    ) -> None:
        card = create_account(
            user,
            name="Cartão",
            kind=AccountKind.CREDIT_CARD,
            opening_date=datetime.date(2026, 1, 1),
        )
        checking.opening_balance_cents = 100_000
        checking.save()
        create_transaction(user, expense(card, food, 30_000, payment_method=PaymentMethod.CREDIT))
        assert account_balances(user)[card.pk] == -30_000  # dívida

        create_transaction(user, transfer(checking, card, 30_000))  # pagamento da fatura
        balances = account_balances(user)
        assert balances[card.pk] == 0
        assert balances[checking.pk] == 70_000
        # A despesa existe uma única vez; o pagamento da fatura não é despesa.
        expenses = list_transactions(user).filter(kind=TransactionKind.EXPENSE)
        assert sum(t.amount_cents for t in expenses) == 30_000

    def test_transactions_before_opening_date_are_history_only(
        self, user: Any, checking: Account, savings: Account, food: Category
    ) -> None:
        checking.opening_date = datetime.date(2026, 10, 1)
        checking.opening_balance_cents = 50_000
        checking.save()
        create_transaction(user, expense(checking, food, 1_000, date=datetime.date(2026, 9, 15)))
        create_transaction(user, expense(checking, food, 2_000, date=datetime.date(2026, 10, 1)))
        assert account_balances(user)[checking.pk] == 48_000

    def test_balance_on_a_past_date(self, user: Any, checking: Account, food: Category) -> None:
        create_transaction(user, expense(checking, food, 1_000, date=datetime.date(2026, 3, 1)))
        create_transaction(user, expense(checking, food, 2_000, date=datetime.date(2026, 5, 1)))
        assert account_balances(user, on=datetime.date(2026, 4, 1))[checking.pk] == -1_000
        assert account_balances(user)[checking.pk] == -3_000


class TestTagMergeMovesTransactions:
    def test_merge_repoints_without_duplicates(
        self, user: Any, checking: Account, food: Category
    ) -> None:
        only_source = create_transaction(user, expense(checking, food, 100, tags=["Supermercado"]))
        both = create_transaction(
            user, expense(checking, food, 200, tags=["Supermercado", "Mercado"])
        )
        target = get_or_create_tag(user, "Mercado")
        merge_tags(get_or_create_tag(user, "Supermercado"), target)
        assert [t.name for t in only_source.tags.all()] == ["Mercado"]
        assert [t.name for t in both.tags.all()] == ["Mercado"]
        assert set(Transaction.objects.filter(tags=target)) == {only_source, both}

    def test_category_can_be_created_and_used(self, user: Any, checking: Account) -> None:
        pets = create_category(user, name="Pets", kind="expense")
        assert create_transaction(user, expense(checking, pets, 100)).category == pets


def test_deleting_user_removes_all_their_data(
    user: Any, other_user: Any, checking: Account, food: Category
) -> None:
    create_transaction(user, expense(checking, food, 100, tags=["Mercado"]))
    user.delete()
    assert not Transaction.objects.filter(owner_id=user.pk).exists()
    assert not Account.objects.filter(owner_id=user.pk).exists()
    assert not Tag.objects.filter(owner_id=user.pk).exists()
    assert Account.objects.for_user(other_user).exists()
