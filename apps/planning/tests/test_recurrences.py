"""Recorrências: geração idempotente de previstas, pular, editar, pausar, excluir, escopo."""

import dataclasses
import datetime
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.ledger.models import Transaction, TransactionStatus
from apps.ledger.services import (
    TransactionData,
    confirm_transaction,
    delete_transaction,
    ensure_default_account,
    restore_transaction,
    transaction_snapshot,
)
from apps.planning.models import Recurrence
from apps.planning.services import (
    HORIZON_DAYS,
    RecurrenceData,
    create_recurrence,
    delete_recurrence,
    materialize,
    set_recurrence_active,
    update_recurrence,
)
from apps.taxonomy.models import Category

pytestmark = pytest.mark.django_db
TODAY = datetime.date(2026, 10, 10)
D = datetime.date
FRAGMENT = {"X-Moneta-Fragment": "1"}


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(timezone, "localdate", lambda *a, **k: TODAY)


def rent_data(user: Any, **changes: Any) -> RecurrenceData:
    entry = TransactionData(
        kind="expense",
        amount_cents=180_000,
        date=TODAY,
        account=ensure_default_account(user),
        category=Category.objects.for_user(user).get(name="Moradia"),
        tags=["Aluguel"],
        payment_method="pix",
        description="Aluguel",
    )
    data = RecurrenceData(entry=entry, frequency="monthly", interval=1, start_date=D(2026, 1, 6))
    entry_changes = {k: v for k, v in changes.items() if hasattr(entry, k) and k != "date"}
    data = dataclasses.replace(data, entry=dataclasses.replace(entry, **entry_changes))
    return dataclasses.replace(data, **{k: v for k, v in changes.items() if k not in entry_changes})


def planned(rec: Recurrence) -> list[datetime.date]:
    return list(
        rec.transactions.filter(status=TransactionStatus.PLANNED)
        .order_by("date")
        .values_list("date", flat=True)
    )


class TestGeneration:
    def test_from_today_through_horizon_never_the_past(self, user: Any) -> None:
        rec = create_recurrence(user, rent_data(user), today=TODAY)
        # Começou em janeiro, mas só gera de hoje em diante (~3 meses).
        assert planned(rec) == [D(2026, 11, 6), D(2026, 12, 6), D(2027, 1, 6)]
        tx = rec.transactions.first()
        assert tx is not None
        assert (tx.amount_cents, tx.description, tx.occurrence_date) == (
            180_000,
            "Aluguel",
            tx.date,
        )
        assert [t.name for t in tx.tags.all()] == ["Aluguel"]
        assert rec.generated_through == TODAY + datetime.timedelta(days=HORIZON_DAYS)

    def test_idempotent_and_horizon_slides(self, user: Any) -> None:
        rec = create_recurrence(user, rent_data(user), today=TODAY)
        assert materialize(user, today=TODAY) == 0
        assert materialize(user, today=D(2026, 11, 10)) == 1  # fev/2027 entra no horizonte
        assert planned(rec)[-1] == D(2027, 2, 6)

    def test_skipped_occurrence_is_not_recreated(self, user: Any) -> None:
        rec = create_recurrence(user, rent_data(user), today=TODAY)
        delete_transaction(rec.transactions.get(date=D(2026, 12, 6)))  # pular dezembro
        materialize(user, today=D(2026, 11, 10))
        assert D(2026, 12, 6) not in planned(rec)

    def test_end_date_stops_generation(self, user: Any) -> None:
        rec = create_recurrence(user, rent_data(user, end_date=D(2026, 12, 31)), today=TODAY)
        assert planned(rec) == [D(2026, 11, 6), D(2026, 12, 6)]

    def test_future_start(self, user: Any) -> None:
        rec = create_recurrence(user, rent_data(user, start_date=D(2026, 12, 20)), today=TODAY)
        assert planned(rec) == [D(2026, 12, 20)]

    def test_planned_never_counts_until_confirmed(self, user: Any) -> None:
        rec = create_recurrence(user, rent_data(user, start_date=TODAY), today=TODAY)
        today_tx = rec.transactions.get(date=TODAY)
        assert today_tx.status == TransactionStatus.PLANNED
        confirm_transaction(today_tx, today=TODAY)
        today_tx.refresh_from_db()
        assert today_tx.status == TransactionStatus.POSTED
        assert today_tx.recurrence == rec  # continua ligada à origem


class TestChanges:
    def test_amount_change_updates_future_planned_and_keeps_skips(self, user: Any) -> None:
        rec = create_recurrence(user, rent_data(user, start_date=TODAY), today=TODAY)
        confirm_transaction(rec.transactions.get(date=TODAY), today=TODAY)
        delete_transaction(rec.transactions.get(date=D(2026, 12, 10)))
        update_recurrence(rec, rent_data(user, start_date=TODAY, amount_cents=195_000), today=TODAY)
        amounts = dict(rec.transactions.values_list("date", "amount_cents"))
        assert amounts == {
            TODAY: 180_000,  # confirmada: histórico não muda
            D(2026, 11, 10): 195_000,
            D(2027, 1, 10): 195_000,
        }
        # Mesmo editando com uma instância antiga, o cursor não volta: dezembro segue pulado.
        stale = Recurrence.objects.get(pk=rec.pk)
        stale.generated_through = TODAY
        update_recurrence(
            stale, rent_data(user, start_date=TODAY, amount_cents=200_000), today=TODAY
        )
        materialize(user, today=TODAY)
        assert D(2026, 12, 10) not in planned(rec)

    def test_schedule_change_rebuilds_future_planned(self, user: Any) -> None:
        rec = create_recurrence(user, rent_data(user), today=TODAY)
        update_recurrence(rec, rent_data(user, start_date=D(2026, 1, 20)), today=TODAY)
        assert planned(rec) == [D(2026, 10, 20), D(2026, 11, 20), D(2026, 12, 20)]

    def test_pause_and_resume(self, user: Any) -> None:
        rec = create_recurrence(user, rent_data(user), today=TODAY)
        set_recurrence_active(rec, active=False, today=TODAY)
        assert planned(rec) == []
        assert materialize(user, today=TODAY) == 0
        set_recurrence_active(rec, active=True, today=D(2026, 11, 10))
        assert planned(rec)[0] == D(2026, 12, 6)

    def test_delete_keeps_confirmed_history(self, user: Any) -> None:
        rec = create_recurrence(user, rent_data(user, start_date=TODAY), today=TODAY)
        confirmed = confirm_transaction(rec.transactions.get(date=TODAY), today=TODAY)
        delete_recurrence(rec)
        confirmed.refresh_from_db()
        assert confirmed.recurrence is None
        assert not Transaction.objects.filter(status=TransactionStatus.PLANNED).exists()

    def test_undo_restores_link_to_recurrence(self, user: Any) -> None:
        rec = create_recurrence(user, rent_data(user), today=TODAY)
        tx = rec.transactions.get(date=D(2026, 11, 6))
        snapshot = transaction_snapshot(tx)
        delete_transaction(tx)
        restored = restore_transaction(user, snapshot)
        assert (restored.recurrence, restored.occurrence_date) == (rec, D(2026, 11, 6))
        assert restored.status == TransactionStatus.PLANNED


class TestValidation:
    def test_category_kind_must_match(self, user: Any) -> None:
        salary = Category.objects.for_user(user).get(name="Salário")
        with pytest.raises(ValidationError) as exc:
            create_recurrence(user, rent_data(user, category=salary), today=TODAY)
        assert "category" in exc.value.message_dict
        assert not Recurrence.objects.exists()

    def test_other_users_account_is_refused(self, user: Any, other_user: Any) -> None:
        theirs = ensure_default_account(other_user)
        with pytest.raises(ValidationError) as exc:
            create_recurrence(user, rent_data(user, account=theirs), today=TODAY)
        assert "account" in exc.value.message_dict

    def test_end_before_start(self, user: Any) -> None:
        with pytest.raises(ValidationError):
            create_recurrence(user, rent_data(user, end_date=D(2025, 1, 1)), today=TODAY)


class TestViews:
    def _form(self, user: Any, **extra: str) -> dict[str, str]:
        return {
            "kind": "expense",
            "amount": "1.800,00",
            "category": str(Category.objects.for_user(user).get(name="Moradia").pk),
            "account": str(ensure_default_account(user).pk),
            "date": "2026-10-06",
            "description": "Aluguel",
            "frequency": "monthly",
            "interval": "1",
            "end_date": "",
            **extra,
        }

    def test_create_and_list(self, client: Client, user: Any) -> None:
        client.force_login(user)
        response = client.post(
            reverse("planning:recurrence_create"), self._form(user), headers=FRAGMENT
        )
        assert response.status_code == 204
        html = client.get(reverse("planning:index")).content.decode()
        assert "Todo mês no dia 6" in html
        assert "próxima em 06/11/2026" in html
        assert "-R$\xa01.800,00" in html  # compromissos mensais
        planned_page = client.get(reverse("ledger:planned_list")).content.decode()
        assert "Aluguel" in planned_page

    def test_form_errors(self, client: Client, user: Any) -> None:
        client.force_login(user)
        html = client.post(
            reverse("planning:recurrence_create"),
            self._form(user, interval="0", end_date="2026-01-01"),
            headers=FRAGMENT,
        ).content.decode()
        assert "A data final deve ser depois da primeira data" in html
        assert not Recurrence.objects.exists()

    def test_edit_toggle_delete(self, client: Client, user: Any) -> None:
        client.force_login(user)
        rec = create_recurrence(user, rent_data(user), today=TODAY)
        edit = client.get(reverse("planning:recurrence_update", args=[rec.pk]), headers=FRAGMENT)
        assert "Salvar alterações" in edit.content.decode()
        client.post(reverse("planning:recurrence_toggle", args=[rec.pk]), headers=FRAGMENT)
        rec.refresh_from_db()
        assert not rec.is_active
        client.post(reverse("planning:recurrence_delete", args=[rec.pk]), headers=FRAGMENT)
        assert not Recurrence.objects.exists()

    @pytest.mark.parametrize(
        "name",
        ["planning:recurrence_update", "planning:recurrence_toggle", "planning:recurrence_delete"],
    )
    def test_other_users_recurrences_are_404(
        self, client: Client, user: Any, other_user: Any, name: str
    ) -> None:
        rec = create_recurrence(user, rent_data(user), today=TODAY)
        client.force_login(other_user)
        assert client.post(reverse(name, args=[rec.pk])).status_code == 404
        assert Recurrence.objects.filter(pk=rec.pk, is_active=True).exists()

    def test_index_is_scoped(self, client: Client, user: Any, other_user: Any) -> None:
        create_recurrence(user, rent_data(user), today=TODAY)
        client.force_login(other_user)
        assert "Nenhuma recorrência" in client.get(reverse("planning:index")).content.decode()
