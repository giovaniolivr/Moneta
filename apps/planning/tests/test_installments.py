"""Compras parceladas: divisão, parcelas no seu mês, previstas futuras, edição, escopo."""

import datetime
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.analytics import selectors as analytics
from apps.ledger.models import AccountKind, Transaction, TransactionStatus
from apps.ledger.services import (
    TransactionData,
    confirm_due,
    create_account,
    delete_transaction,
    restore_transaction,
    transaction_snapshot,
)
from apps.planning.domain.installments import due_dates, label, split
from apps.planning.models import InstallmentPlan
from apps.planning.services import (
    create_installment_plan,
    delete_installment_plan,
    update_installment_plan,
)
from apps.taxonomy.models import Category

pytestmark = pytest.mark.django_db
TODAY = datetime.date(2026, 10, 10)
D = datetime.date
FRAGMENT = {"X-Moneta-Fragment": "1"}


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(timezone, "localdate", lambda *a, **k: TODAY)


@pytest.fixture
def card(user: Any) -> Any:
    return create_account(
        user, name="Cartão", kind=AccountKind.CREDIT_CARD, opening_date=D(2026, 1, 1)
    )


def notebook(user: Any, card: Any, **kw: Any) -> TransactionData:
    return TransactionData(
        **{
            "kind": "expense",
            "amount_cents": 400_000,
            "date": D(2026, 8, 20),
            "account": card,
            "category": Category.objects.for_user(user).get(name="Compras"),
            "tags": ["Eletrônicos"],
            "payment_method": "credit",
            "description": "Notebook",
            **kw,
        }
    )


class TestDomain:
    def test_split_puts_remainder_on_first(self) -> None:
        assert split(100_000, 3) == [33_334, 33_333, 33_333]
        assert sum(split(123_457, 12)) == 123_457
        with pytest.raises(ValueError, match="entre 2 e 48"):
            split(1_000, 1)
        with pytest.raises(ValueError, match="centavo"):
            split(2, 3)

    def test_due_dates_and_label(self) -> None:
        assert due_dates(D(2026, 1, 31), 3) == [D(2026, 1, 31), D(2026, 2, 28), D(2026, 3, 31)]
        assert label("Notebook", 3, 12) == "Notebook (3/12)"


class TestService:
    def test_each_installment_in_its_month(self, user: Any, card: Any) -> None:
        plan = create_installment_plan(user, notebook(user, card), count=4, today=TODAY)
        rows = list(
            plan.transactions.order_by("installment_number").values_list(
                "installment_number", "date", "amount_cents", "status", "description"
            )
        )
        posted, planned = TransactionStatus.POSTED, TransactionStatus.PLANNED
        assert rows == [
            (1, D(2026, 8, 20), 100_000, posted, "Notebook (1/4)"),
            (2, D(2026, 9, 20), 100_000, posted, "Notebook (2/4)"),
            (3, D(2026, 10, 20), 100_000, planned, "Notebook (3/4)"),
            (4, D(2026, 11, 20), 100_000, planned, "Notebook (4/4)"),
        ]
        # Análises: R$ 1.000 por mês, não R$ 4.000 em agosto.
        assert analytics.totals(user, D(2026, 8, 1), D(2026, 8, 31)).expense_cents == 100_000
        assert analytics.totals(user, D(2026, 10, 1), D(2026, 10, 31)).expense_cents == 0
        tx = plan.transactions.get(installment_number=1)
        assert [t.name for t in tx.tags.all()] == ["Eletrônicos"]

    def test_only_expenses_and_valid_counts(self, user: Any, card: Any) -> None:
        salary = Category.objects.for_user(user).get(name="Salário")
        with pytest.raises(ValidationError, match="Só despesas"):
            create_installment_plan(
                user, notebook(user, card, kind="income", category=salary), count=3, today=TODAY
            )
        with pytest.raises(ValidationError):
            create_installment_plan(user, notebook(user, card), count=60, today=TODAY)
        assert not InstallmentPlan.objects.exists()
        assert not Transaction.objects.exists()

    def test_update_applies_to_every_installment(self, user: Any, card: Any) -> None:
        plan = create_installment_plan(user, notebook(user, card), count=3, today=TODAY)
        leisure = Category.objects.for_user(user).get(name="Lazer")
        update_installment_plan(
            plan, description="Notebook novo", category=leisure, tag_names=["Trabalho"], notes=""
        )
        for tx in plan.transactions.all():
            assert tx.description.startswith("Notebook novo (")
            assert tx.category == leisure
            assert [t.name for t in tx.tags.all()] == ["Trabalho"]

    def test_delete_keeps_posted_installments(self, user: Any, card: Any) -> None:
        plan = create_installment_plan(user, notebook(user, card), count=4, today=TODAY)
        delete_installment_plan(plan)
        left = Transaction.objects.for_user(user)
        assert left.count() == 2
        assert not left.filter(status=TransactionStatus.PLANNED).exists()
        assert not left.filter(installment_plan__isnull=False).exists()

    def test_confirm_due(self, user: Any, card: Any) -> None:
        create_installment_plan(user, notebook(user, card), count=4, today=D(2026, 8, 20))
        assert confirm_due(user, today=TODAY) == 1  # só setembro já chegou (outubro é dia 20)
        assert confirm_due(user, today=TODAY) == 0

    def test_undo_restores_installment_link(self, user: Any, card: Any) -> None:
        plan = create_installment_plan(user, notebook(user, card), count=3, today=TODAY)
        tx = plan.transactions.get(installment_number=2)
        snapshot = transaction_snapshot(tx)
        delete_transaction(tx)
        restored = restore_transaction(user, snapshot)
        assert (restored.installment_plan, restored.installment_number) == (plan, 2)


class TestViews:
    def _form(self, user: Any, card: Any, **extra: str) -> dict[str, str]:
        return {
            "kind": "expense",
            "amount": "1.200,00",
            "category": str(Category.objects.for_user(user).get(name="Compras").pk),
            "account": str(card.pk),
            "date": "2026-10-01",
            "description": "Geladeira",
            "installments": "12",
            **extra,
        }

    def test_create_from_transaction_form(self, client: Client, user: Any, card: Any) -> None:
        client.force_login(user)
        response = client.post(
            reverse("ledger:transaction_create"), self._form(user, card), headers=FRAGMENT
        )
        assert response.status_code == 204
        plan = InstallmentPlan.objects.get()
        assert (plan.count, plan.total_cents) == (12, 120_000)
        assert plan.transactions.count() == 12
        assert plan.transactions.filter(status=TransactionStatus.POSTED).count() == 1
        page = client.get(reverse("planning:index")).content.decode()
        assert "1/12 pagas" in page
        assert "falta R$\xa01.100,00" in page

    def test_installments_ignored_for_income_and_transfer(
        self, client: Client, user: Any, card: Any
    ) -> None:
        client.force_login(user)
        salary = Category.objects.for_user(user).get(name="Salário")
        form = self._form(user, card, kind="income", category=str(salary.pk))
        client.post(reverse("ledger:transaction_create"), form, headers=FRAGMENT)
        assert not InstallmentPlan.objects.exists()
        assert Transaction.objects.get().amount_cents == 120_000

    def test_installments_field_only_when_creating(
        self, client: Client, user: Any, card: Any
    ) -> None:
        client.force_login(user)
        new = client.get(reverse("ledger:transaction_create"), headers=FRAGMENT).content.decode()
        assert 'name="installments"' in new
        plan = create_installment_plan(user, notebook(user, card), count=3, today=TODAY)
        tx = plan.transactions.first()
        assert tx is not None
        edit = client.get(
            reverse("ledger:transaction_update", args=[tx.pk]), headers=FRAGMENT
        ).content.decode()
        assert 'name="installments"' not in edit
        recurrence = client.get(
            reverse("planning:recurrence_create"), headers=FRAGMENT
        ).content.decode()
        assert 'name="installments"' not in recurrence

    def test_edit_and_delete_plan(self, client: Client, user: Any, card: Any) -> None:
        client.force_login(user)
        plan = create_installment_plan(user, notebook(user, card), count=3, today=TODAY)
        url = reverse("planning:installment_update", args=[plan.pk])
        category = str(plan.category_id)
        response = client.post(
            url,
            {"description": "Notebook Dell", "category": category, "tags": ""},
            headers=FRAGMENT,
        )
        assert response.status_code == 204
        assert plan.transactions.filter(description="Notebook Dell (1/3)").exists()
        client.post(reverse("planning:installment_delete", args=[plan.pk]), headers=FRAGMENT)
        assert not InstallmentPlan.objects.exists()

    def test_confirm_all_button(self, client: Client, user: Any, card: Any) -> None:
        client.force_login(user)
        create_installment_plan(user, notebook(user, card), count=4, today=D(2026, 7, 1))
        html = client.get(reverse("ledger:planned_list")).content.decode()
        assert "Confirmar todas (2)" in html
        client.post(reverse("ledger:planned_confirm_due"))
        assert not Transaction.objects.filter(
            status=TransactionStatus.PLANNED, date__lte=TODAY
        ).exists()

    @pytest.mark.parametrize("name", ["planning:installment_update", "planning:installment_delete"])
    def test_other_users_plans_are_404(
        self, client: Client, user: Any, other_user: Any, card: Any, name: str
    ) -> None:
        plan = create_installment_plan(user, notebook(user, card), count=3, today=TODAY)
        client.force_login(other_user)
        assert client.post(reverse(name, args=[plan.pk])).status_code == 404
        assert InstallmentPlan.objects.filter(pk=plan.pk).exists()

    def test_confirm_all_is_scoped(
        self, client: Client, user: Any, other_user: Any, card: Any
    ) -> None:
        create_installment_plan(user, notebook(user, card), count=4, today=D(2026, 7, 1))
        client.force_login(other_user)
        client.post(reverse("ledger:planned_confirm_due"))
        assert (
            Transaction.objects.filter(status=TransactionStatus.PLANNED, date__lte=TODAY).count()
            == 2
        )
