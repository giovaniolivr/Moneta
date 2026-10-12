"""Exportação e importação: round-trip, idempotência, casamento por nome, tudo-ou-nada, escopo."""

import copy
import datetime
import json
import uuid
from decimal import Decimal
from typing import Any

import pytest
from django.utils import timezone

from apps.analytics import selectors as analytics
from apps.goals.models import Goal
from apps.goals.services import create_goal
from apps.investments.models import InvestmentProduct
from apps.investments.services import ProductData, create_product
from apps.ledger.models import Account, AccountKind, Transaction
from apps.ledger.selectors import account_balances
from apps.ledger.services import (
    CardBilling,
    TransactionData,
    create_account,
    create_transaction,
    ensure_default_account,
    update_account,
)
from apps.planning.models import Budget, InstallmentPlan, Recurrence
from apps.planning.services import (
    RecurrenceData,
    create_budget,
    create_installment_plan,
    create_recurrence,
)
from apps.portability import schema
from apps.portability.schema import InvalidFileError
from apps.portability.selectors import export_payload
from apps.portability.services import ImportFailedError, import_payload, parse_file
from apps.taxonomy.models import Category, Tag, TagAlias
from apps.taxonomy.services import add_tag_alias, get_or_create_tag, set_category_archived

pytestmark = pytest.mark.django_db
D = datetime.date


@pytest.fixture
def data(user: Any) -> dict[str, Any]:
    checking = ensure_default_account(user)
    card = create_account(
        user, name="Cartão", kind=AccountKind.CREDIT_CARD, opening_date=D(2026, 1, 1)
    )
    update_account(card, billing=CardBilling(closing_day=31, due_day=10, payment_account=checking))
    caixinha = create_product(
        user, ProductData("Caixinha", "cdi", Decimal("100"), "taxed", daily_liquidity=True)
    )
    savings = create_account(
        user, name="Caixinha", kind=AccountKind.SAVINGS, opening_balance_cents=100_000
    )
    update_account(savings, product=caixinha)
    cats = {c.name: c for c in Category.objects.for_user(user)}
    add_tag_alias(get_or_create_tag(user, "Mercado"), "supermercado")

    def add(kind: str, cents: int, day: datetime.date, **kw: Any) -> None:
        create_transaction(user, TransactionData(kind=kind, amount_cents=cents, date=day, **kw))

    add("income", 500_000, D(2026, 9, 5), account=checking, category=cats["Salário"])
    add(
        "expense",
        12_345,
        D(2026, 9, 8),
        account=card,
        category=cats["Alimentação"],
        tags=["Mercado", "Família"],
        payment_method="credit",
        description="=compra do mês",
        notes="nota\ncom quebra",
    )
    add("expense", 4_000, D(2026, 9, 20), account=checking, category=cats["Lazer"])
    add("transfer", 12_345, D(2026, 10, 1), account=checking, destination_account=card)
    add("transfer", 50_000, D(2026, 10, 2), account=checking, destination_account=savings)
    add(
        "expense",
        90_000,
        D(2027, 1, 15),
        account=checking,
        category=cats["Moradia"],
        status="planned",
    )
    create_recurrence(
        user,
        RecurrenceData(
            entry=TransactionData(
                kind="expense",
                amount_cents=9_990,
                date=D(2026, 1, 15),
                account=card,
                category=cats["Assinaturas"],
                tags=["Internet"],
            ),
            frequency="monthly",
            interval=1,
            start_date=D(2026, 1, 15),
        ),
        today=timezone.localdate(),
    )
    create_installment_plan(
        user,
        TransactionData(
            kind="expense",
            amount_cents=100_000,
            date=D(2026, 9, 1),
            account=card,
            category=cats["Compras"],
            tags=["Eletrônicos"],
            description="Celular",
        ),
        count=10,
        today=timezone.localdate(),
    )
    create_budget(user, category=cats["Alimentação"], amount_cents=150_000)
    create_budget(user, category=None, amount_cents=600_000)
    create_goal(user, name="Reserva", target_cents=2_000_000, account=savings)
    set_category_archived(cats["Lazer"], archived=True)
    return {"checking": checking, "card": card, "savings": savings, "cats": cats}


def _without_timestamp(payload: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in payload.items() if k != "exported_at"}


def _wipe(user: Any) -> None:
    Transaction.objects.for_user(user).delete()
    Recurrence.objects.for_user(user).delete()
    InstallmentPlan.objects.for_user(user).delete()
    Budget.objects.for_user(user).delete()
    Goal.objects.for_user(user).delete()
    InvestmentProduct.objects.for_user(user).delete()
    TagAlias.objects.for_user(user).delete()
    Tag.objects.for_user(user).delete()
    Account.objects.for_user(user).delete()
    Category.objects.for_user(user).delete()


def test_round_trip_preserves_everything(user: Any, data: Any) -> None:
    before = export_payload(user)
    balances = account_balances(user)
    totals = analytics.totals(user, None, None)
    _wipe(user)

    report = import_payload(user, json.loads(json.dumps(before)))

    assert report.recurrences.created == 1
    assert report.installment_plans.created == 1
    assert report.budgets.created == 2
    assert report.goals.created == 1
    assert report.products.created == 1
    assert Account.objects.for_user(user).get(name="Caixinha").product is not None
    assert Transaction.objects.for_user(user).filter(installment_number=10).exists()
    assert report.transactions.created == len(before["transactions"])
    assert Transaction.objects.for_user(user).filter(recurrence__isnull=False).exists()
    assert report.accounts.created == 3
    assert _without_timestamp(export_payload(user)) == _without_timestamp(before)
    assert account_balances(user) == balances
    assert analytics.totals(user, None, None) == totals
    assert Category.objects.for_user(user).get(name="Lazer").is_archived
    assert Tag.objects.for_user(user).get(name="Mercado").aliases.get().normalized_alias == (
        "supermercado"
    )


def test_import_is_idempotent(user: Any, data: Any) -> None:
    report = import_payload(user, export_payload(user))
    assert report.changes == 0
    assert report.transactions.unchanged == Transaction.objects.for_user(user).count()


def _tx(payload: dict[str, Any], day: str, cents: int) -> dict[str, Any]:
    """Movimentação do arquivo pela data e valor (a ordem da lista não importa)."""
    return next(
        t for t in payload["transactions"] if t["date"] == day and t["amount_cents"] == cents
    )


def test_file_wins_on_update(user: Any, data: Any) -> None:
    payload = export_payload(user)
    salary, groceries = _tx(payload, "2026-09-05", 500_000), _tx(payload, "2026-09-08", 12_345)
    salary["description"] = "Salário de setembro"
    groceries["tags"] = []
    payload["accounts"][1]["name"] = "Cartão Nubank"
    report = import_payload(user, payload)
    assert report.transactions.updated == 2
    assert report.accounts.updated == 1
    mine = Transaction.objects.for_user(user)
    assert mine.get(pk=salary["id"]).description == "Salário de setembro"
    assert not mine.get(pk=groceries["id"]).tags.exists()
    data["card"].refresh_from_db()
    assert data["card"].name == "Cartão Nubank"


def test_same_name_reuses_existing_objects(user: Any) -> None:
    """Arquivo de outra instalação: UUIDs novos, mas "Alimentação" já existe aqui."""
    create_account(user, name="Nubank", kind=AccountKind.CHECKING)
    accounts_before = Account.objects.for_user(user).count()
    account_id, category_id, tag_id = (str(uuid.uuid4()) for _ in range(3))
    payload = {
        "format": "moneta",
        "schema_version": 1,
        "accounts": [
            {
                "id": account_id,
                "name": "NUBANK",  # mesmo nome normalizado da conta existente
                "kind": "checking",
                "opening_balance_cents": 0,
                "opening_date": "2026-01-01",
            }
        ],
        "categories": [{"id": category_id, "name": "ALIMENTACAO", "kind": "expense"}],
        "tags": [{"id": tag_id, "name": "Mercado"}],
        "transactions": [
            {
                "id": str(uuid.uuid4()),
                "kind": "expense",
                "amount_cents": 1_000,
                "date": "2026-09-01",
                "account": account_id,
                "category": category_id,
                "tags": [tag_id],
            }
        ],
    }
    report = import_payload(user, payload)
    assert report.accounts.created == 0
    assert report.categories.created == 0
    assert report.tags.created == 1
    assert Account.objects.for_user(user).count() == accounts_before
    tx = Transaction.objects.for_user(user).get()
    assert tx.category is not None
    assert tx.category.name == "ALIMENTACAO"  # o arquivo vence (nome de exibição)
    assert Category.objects.for_user(user).filter(normalized_name="alimentacao").count() == 1


def test_all_or_nothing_with_every_error_listed(user: Any, data: Any) -> None:
    payload = export_payload(user)
    _wipe(user)
    _tx(payload, "2026-09-05", 500_000)["amount_cents"] = 0
    _tx(payload, "2026-09-20", 4_000)["date"] = "32/13/2026"
    _tx(payload, "2026-09-08", 12_345)["category"] = str(uuid.uuid4())
    with pytest.raises(ImportFailedError) as exc:
        import_payload(user, payload)
    errors = sorted(exc.value.errors)
    assert len(errors) == 3
    assert any(e.endswith("(2026-09-05): O valor deve ser maior que zero.") for e in errors)
    assert any("data “32/13/2026” inválida" in e for e in errors)
    assert any("(2026-09-08): categoria" in e for e in errors)
    assert Transaction.objects.for_user(user).count() == 0
    assert Account.objects.for_user(user).count() == 0  # contas válidas também não ficaram


def test_dry_run_writes_nothing(user: Any, data: Any) -> None:
    payload = export_payload(user)
    _wipe(user)
    report = import_payload(user, payload, dry_run=True)
    assert report.transactions.created == len(payload["transactions"])
    assert Transaction.objects.for_user(user).count() == 0


def test_ids_of_another_user_are_refused(user: Any, other_user: Any, data: Any) -> None:
    payload = export_payload(user)
    with pytest.raises(ImportFailedError) as exc:
        import_payload(other_user, payload)
    assert all("outro usuário" in e for e in exc.value.errors)
    assert Transaction.objects.for_user(other_user).count() == 0
    # E os dados do dono continuam intactos.
    assert Transaction.objects.for_user(user).count() == len(payload["transactions"])


def test_export_is_scoped_and_anonymous(user: Any, other_user: Any, data: Any) -> None:
    payload = export_payload(other_user)
    assert payload["transactions"] == []
    assert user.username not in json.dumps(export_payload(user))


class TestFileFormat:
    def test_rejects_non_moneta_files(self) -> None:
        with pytest.raises(InvalidFileError, match="não é um JSON"):
            parse_file(b"\x00\xff")
        with pytest.raises(InvalidFileError, match="não é uma exportação"):
            parse_file(b'{"format": "outro"}')
        with pytest.raises(InvalidFileError, match="Versão"):
            parse_file(b'{"format": "moneta", "schema_version": true}')
        with pytest.raises(InvalidFileError, match="seção"):
            parse_file(b'{"format": "moneta", "schema_version": 1, "tags": {}}')

    def test_newer_version_asks_for_update(self) -> None:
        with pytest.raises(InvalidFileError, match="mais nova"):
            parse_file(b'{"format": "moneta", "schema_version": 99}')

    def test_old_versions_are_migrated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def v1_to_v2(payload: dict[str, Any]) -> dict[str, Any]:
            migrated = copy.deepcopy(payload)
            migrated["tags"] = [{"id": t, "name": t} for t in payload["tag_names"]]
            return migrated

        monkeypatch.setattr(schema, "SCHEMA_VERSION", 3)
        monkeypatch.setitem(schema.MIGRATIONS, 2, v1_to_v2)
        payload = parse_file(
            b'\xef\xbb\xbf{"format": "moneta", "schema_version": 1, "tag_names": ["x"]}'
        )
        assert payload["schema_version"] == 3
        assert payload["tags"] == [{"id": "x", "name": "x"}]

    def test_v1_files_are_all_posted(self) -> None:
        raw = {"format": "moneta", "schema_version": 1, "transactions": [{"id": "x"}]}
        payload = parse_file(json.dumps(raw).encode())
        assert payload["schema_version"] == schema.SCHEMA_VERSION
        assert payload["transactions"][0]["status"] == "posted"


def test_budget_matches_existing_category(user: Any, data: Any) -> None:
    """Arquivo de outra instalação: UUID novo, mas a categoria já tem orçamento → atualiza."""
    payload = export_payload(user)
    food = next(b for b in payload["budgets"] if b["category"])
    food["id"] = str(uuid.uuid4())
    food["amount_cents"] = 175_000
    report = import_payload(user, payload)
    assert (report.budgets.created, report.budgets.updated) == (0, 1)
    assert Budget.objects.for_user(user).get(category__isnull=False).amount_cents == 175_000
