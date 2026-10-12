"""Desfazer a exclusão de uma movimentação."""

import datetime
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from apps.ledger.models import Transaction
from apps.ledger.services import (
    TransactionData,
    create_transaction,
    delete_transaction,
    ensure_default_account,
    restore_transaction,
    transaction_snapshot,
)
from apps.taxonomy.models import Category
from apps.taxonomy.services import delete_category

pytestmark = pytest.mark.django_db
FRAGMENT = {"X-Moneta-Fragment": "1"}


@pytest.fixture
def tx(user: Any) -> Transaction:
    food = Category.objects.for_user(user).get(name="Alimentação")
    return create_transaction(
        user,
        TransactionData(
            kind="expense",
            amount_cents=4_590,
            date=datetime.date(2026, 9, 3),
            account=ensure_default_account(user),
            category=food,
            tags=["Mercado", "Família"],
            payment_method="pix",
            description="Feira",
            notes="orgânicos",
        ),
    )


def test_delete_offers_undo_and_restores_everything(
    client: Client, user: Any, tx: Transaction
) -> None:
    client.force_login(user)
    pk, before = tx.pk, transaction_snapshot(tx)
    assert (
        client.post(reverse("ledger:transaction_delete", args=[pk]), headers=FRAGMENT).status_code
        == 204
    )

    html = client.get(reverse("ledger:transaction_list")).content.decode()
    assert "excluída" in html
    assert reverse("ledger:transaction_restore") in html  # botão "Desfazer" na mensagem

    response = client.post(reverse("ledger:transaction_restore"), {"next": "/movimentacoes/"})
    assert response["Location"] == "/movimentacoes/"
    restored = Transaction.objects.for_user(user).get(pk=pk)
    assert transaction_snapshot(restored) == before  # mesmo id, campos e tags

    follow = client.post(reverse("ledger:transaction_restore"), follow=True).content.decode()
    assert "Não há exclusão para desfazer" in follow


def test_undo_message_only_after_delete(client: Client, user: Any, tx: Transaction) -> None:
    client.force_login(user)
    html = client.get(reverse("ledger:transaction_list")).content.decode()
    assert reverse("ledger:transaction_restore") not in html


def test_cannot_restore_into_another_user(user: Any, other_user: Any, tx: Transaction) -> None:
    snapshot = transaction_snapshot(tx)
    delete_transaction(tx)
    with pytest.raises(ValidationError, match="conta não existe"):
        restore_transaction(other_user, snapshot)
    assert not Transaction.objects.filter(pk=snapshot["id"]).exists()


def test_reports_missing_category(user: Any, tx: Transaction) -> None:
    snapshot = transaction_snapshot(tx)
    category = tx.category
    delete_transaction(tx)
    assert category is not None
    delete_category(category)
    with pytest.raises(ValidationError, match="categoria não existe"):
        restore_transaction(user, snapshot)


def test_restore_twice_is_refused(user: Any, tx: Transaction) -> None:
    snapshot = transaction_snapshot(tx)
    delete_transaction(tx)
    restore_transaction(user, snapshot)
    with pytest.raises(ValidationError, match="já foi restaurada"):
        restore_transaction(user, snapshot)
