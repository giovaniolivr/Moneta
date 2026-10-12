import datetime
from io import StringIO
from typing import Any

import pytest
from django.core.management import CommandError, call_command
from django.db.models import Sum
from django.utils import timezone

from apps.ledger.demo import DEMO_USERNAME, seed_demo
from apps.ledger.models import AccountKind, Transaction, TransactionKind, TransactionStatus
from apps.ledger.selectors import account_balances
from apps.users.models import User

pytestmark = pytest.mark.django_db

TODAY = datetime.date(2026, 10, 15)


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(timezone, "localdate", lambda *a, **k: TODAY)


def test_generates_months_of_plausible_data() -> None:
    summary = seed_demo(today=TODAY)
    user = User.objects.get(username=DEMO_USERNAME)
    txs = Transaction.objects.for_user(user).filter(status=TransactionStatus.POSTED)
    assert summary.transactions == txs.count() > 150
    assert summary.first_date == datetime.date(2026, 5, 1)
    assert not txs.filter(date__gt=TODAY).exists()
    # Contas fixas viram recorrências: previstas só de hoje em diante, ligadas à origem.
    planned = Transaction.objects.for_user(user).filter(status=TransactionStatus.PLANNED)
    assert planned.exists()
    assert not planned.filter(date__lt=TODAY).exists()
    assert not planned.filter(recurrence__isnull=True).exists()
    assert txs.filter(kind=TransactionKind.TRANSFER).exists()
    assert user.is_superuser


def test_card_bills_match_previous_month_spending() -> None:
    seed_demo(today=TODAY)
    user = User.objects.get(username=DEMO_USERNAME)
    on_card = Transaction.objects.for_user(user).filter(account__kind=AccountKind.CREDIT_CARD)
    sept_spend = on_card.filter(
        kind=TransactionKind.EXPENSE, date__year=2026, date__month=9
    ).aggregate(s=Sum("amount_cents"))["s"]
    october_bill = Transaction.objects.for_user(user).get(
        kind=TransactionKind.TRANSFER, description="Pagamento da fatura", date__month=10
    )
    assert october_bill.amount_cents == sept_spend


def test_is_deterministic() -> None:
    first = seed_demo(today=TODAY, months=2, username="demo-a")
    second = seed_demo(today=TODAY, months=2, username="demo-b")
    a, b = (User.objects.get(username=n) for n in ("demo-a", "demo-b"))
    assert first.transactions == second.transactions
    assert sorted(account_balances(a).values()) == sorted(account_balances(b).values())


def test_command_refuses_non_demo_database() -> None:
    with pytest.raises(CommandError, match="não parece ser de demonstração"):
        call_command("seed_demo")


def test_command_recreates_demo_user(settings: Any) -> None:
    out = StringIO()
    call_command("seed_demo", "--force", "--months", "2", stdout=out)
    first_id = User.objects.get(username=DEMO_USERNAME).pk
    call_command("seed_demo", "--force", "--months", "2", stdout=out)
    assert User.objects.filter(username=DEMO_USERNAME).count() == 1
    assert User.objects.get(username=DEMO_USERNAME).pk != first_id
    assert "removidos" in out.getvalue()
