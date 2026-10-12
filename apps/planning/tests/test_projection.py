"""Projeção de saldo e faturas previstas."""

import datetime
import uuid
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.ledger.models import Account, AccountKind
from apps.ledger.services import (
    CardBilling,
    TransactionData,
    create_account,
    create_transaction,
    ensure_default_account,
    update_account,
)
from apps.planning.domain.projection import (
    Billing,
    Event,
    PendingPayment,
    day_in_month,
    project,
)
from apps.planning.selectors import projection_data
from apps.planning.services import create_installment_plan
from apps.taxonomy.models import Category

D = datetime.date
TODAY = D(2026, 10, 12)
CHECKING, CARD = uuid.uuid4(), uuid.uuid4()
FRAGMENT = {"X-Moneta-Fragment": "1"}


class TestDomain:
    def test_billing_dates(self) -> None:
        end_of_month = Billing(CARD, CHECKING, closing_day=31, due_day=10)
        assert end_of_month.closing_on_or_after(D(2026, 2, 3)) == D(2026, 2, 28)
        assert end_of_month.due_for(D(2026, 10, 31)) == D(2026, 11, 10)  # vence no mês seguinte
        assert end_of_month.last_closing_before(D(2026, 10, 12)) == D(2026, 9, 30)
        mid = Billing(CARD, CHECKING, closing_day=5, due_day=15)
        assert mid.due_for(D(2026, 10, 5)) == D(2026, 10, 15)  # vence no mesmo mês
        assert mid.last_closing_before(D(2026, 10, 5)) == D(2026, 9, 5)
        assert day_in_month(2028, 2, 31) == D(2028, 2, 29)

    def test_planned_events_and_overdue_count_today(self) -> None:
        proj = project(
            today=TODAY,
            end=D(2026, 10, 20),
            opening={CHECKING: 100_000},
            events=[
                Event(D(2026, 10, 5), CHECKING, -30_000),  # vencida, ainda não confirmada
                Event(D(2026, 10, 15), CHECKING, -90_000),
            ],
        )
        assert proj.balance_on(CHECKING, TODAY) == 70_000
        assert proj.balance_on(CHECKING, D(2026, 10, 15)) == -20_000
        assert proj.first_negative(CHECKING) == D(2026, 10, 15)
        assert proj.lowest(CHECKING) == (D(2026, 10, 15), -20_000)

    def test_card_statement_is_paid_by_payer_on_due_date(self) -> None:
        billing = Billing(CARD, CHECKING, closing_day=31, due_day=10)
        proj = project(
            today=TODAY,
            end=D(2026, 11, 30),
            opening={CHECKING: 500_000, CARD: -20_000},  # já gastou R$ 200 no ciclo
            events=[Event(D(2026, 10, 20), CARD, -42_000)],  # parcela prevista
            billings=[billing],
        )
        [october, november] = proj.statements
        assert (october.closing, october.due) == (D(2026, 10, 31), D(2026, 11, 10))
        assert (october.amount_cents, october.planned_cents) == (62_000, 42_000)
        assert proj.balance_on(CHECKING, D(2026, 11, 9)) == 500_000
        assert proj.balance_on(CHECKING, D(2026, 11, 10)) == 438_000  # pagou a fatura
        assert proj.balance_on(CARD, D(2026, 11, 10)) == 0
        assert november.amount_cents == 0

    def test_pending_payment_of_closed_statement(self) -> None:
        billing = Billing(CARD, CHECKING, closing_day=5, due_day=15)
        proj = project(
            today=TODAY,
            end=D(2026, 10, 20),
            opening={CHECKING: 100_000, CARD: -30_000},
            events=[],
            billings=[billing],
            pending=[PendingPayment(CARD, D(2026, 10, 5), D(2026, 10, 15), 30_000)],
        )
        assert proj.statements[0].closed
        assert proj.balance_on(CHECKING, D(2026, 10, 15)) == 70_000


@pytest.mark.django_db
class TestIntegration:
    @pytest.fixture(autouse=True)
    def _fixed_today(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(timezone, "localdate", lambda *a, **k: TODAY)

    @pytest.fixture
    def world(self, user: Any) -> dict[str, Any]:
        checking = ensure_default_account(user)
        update_account(checking, opening_balance_cents=300_000, opening_date=D(2026, 9, 1))
        card = create_account(
            user,
            name="Cartão",
            kind=AccountKind.CREDIT_CARD,
            opening_date=D(2026, 9, 1),
            billing=CardBilling(closing_day=5, due_day=15, payment_account=checking),
        )
        food = Category.objects.for_user(user).get(name="Alimentação")

        def spend(cents: int, day: datetime.date) -> None:
            create_transaction(
                user,
                TransactionData(
                    kind="expense", amount_cents=cents, date=day, account=card, category=food
                ),
            )

        spend(50_000, D(2026, 10, 1))  # fatura que fechou em 05/10
        create_transaction(  # pagamento parcial dessa fatura
            user,
            TransactionData(
                kind="transfer",
                amount_cents=20_000,
                date=D(2026, 10, 8),
                account=checking,
                destination_account=card,
            ),
        )
        spend(10_000, D(2026, 10, 10))  # já na fatura que fecha em 05/11
        create_installment_plan(
            user,
            TransactionData(
                kind="expense",
                amount_cents=60_000,
                date=D(2026, 10, 20),
                account=card,
                category=food,
            ),
            count=3,
            today=TODAY,
        )
        return {"checking": checking, "card": card}

    def test_statements_and_checking_projection(self, user: Any, world: Any) -> None:
        data = projection_data(user, TODAY)
        proj = data.projection
        card, checking = world["card"].pk, world["checking"].pk
        closed, *upcoming = [s for s in proj.statements if s.card_id == card]
        assert (closed.closed, closed.due, closed.amount_cents) == (True, D(2026, 10, 15), 30_000)
        november = upcoming[0]
        assert (november.closing, november.amount_cents, november.planned_cents) == (
            D(2026, 11, 5),
            30_000,  # R$ 100 lançados + R$ 200 da 1ª parcela prevista
            20_000,
        )
        assert proj.balance_on(checking, TODAY) == 280_000
        assert proj.balance_on(checking, D(2026, 10, 15)) == 250_000
        assert proj.balance_on(checking, D(2026, 11, 15)) == 220_000
        assert data.unconfigured_cards == []

    def test_page(self, client: Client, user: Any, world: Any) -> None:
        client.force_login(user)
        html = client.get(reverse("planning:projection")).content.decode()
        assert "Faturas previstas" in html
        assert "Fechada em 05/10 · falta pagar" in html
        assert "inclui R$\xa0200,00 em previstas" in html
        assert 'id="data-projection"' in html

    def test_negative_alert_and_unconfigured_card(
        self, client: Client, user: Any, world: Any
    ) -> None:
        update_account(world["card"], billing=CardBilling())
        rent = Category.objects.for_user(user).get(name="Moradia")
        create_transaction(
            user,
            TransactionData(
                kind="expense",
                amount_cents=400_000,
                date=D(2026, 11, 1),
                account=world["checking"],
                category=rent,
                status="planned",
            ),
        )
        client.force_login(user)
        html = client.get(reverse("planning:projection")).content.decode()
        assert "deve ficar negativa em 01/11" in html
        assert "informe fechamento, vencimento e conta pagadora" in html

    def test_scoped(self, client: Client, other_user: Any, world: Any) -> None:
        client.force_login(other_user)
        html = client.get(reverse("planning:projection")).content.decode()
        assert "Cartão" not in html
        assert "Faturas previstas" not in html


@pytest.mark.django_db
class TestCardSettings:
    def test_validation(self, user: Any, other_user: Any) -> None:
        checking = ensure_default_account(user)
        card = create_account(user, name="Cartão", kind=AccountKind.CREDIT_CARD)
        other_card = create_account(user, name="Outro", kind=AccountKind.CREDIT_CARD)
        with pytest.raises(ValidationError, match="não seja cartão"):
            update_account(card, billing=CardBilling(5, 15, other_card))
        with pytest.raises(ValidationError, match="inválida"):
            update_account(card, billing=CardBilling(5, 15, ensure_default_account(other_user)))
        with pytest.raises(ValidationError, match="entre 1 e 31"):
            update_account(card, billing=CardBilling(0, 15, checking))
        update_account(card, billing=CardBilling(5, 15, checking))
        assert card.has_billing
        # Conta que não é cartão não guarda configuração de fatura.
        update_account(checking, billing=CardBilling(5, 15, None))
        checking.refresh_from_db()
        assert checking.statement_closing_day is None

    def test_form(self, client: Client, user: Any) -> None:
        checking = ensure_default_account(user)
        client.force_login(user)
        response = client.post(
            reverse("ledger:account_create"),
            {
                "name": "Nubank Roxinho",
                "kind": "credit_card",
                "opening_balance": "",
                "opening_date": "2026-10-01",
                "closing_day": "3",
                "due_day": "10",
                "payment_account": str(checking.pk),
            },
            headers=FRAGMENT,
        )
        assert response.status_code == 204
        card = Account.objects.get(name="Nubank Roxinho")
        assert (card.statement_closing_day, card.payment_due_day, card.payment_account) == (
            3,
            10,
            checking,
        )
