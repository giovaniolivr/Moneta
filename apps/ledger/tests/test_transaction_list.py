import datetime
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.ledger.filters import TransactionFilter, period_range
from apps.ledger.models import AccountKind, Transaction
from apps.ledger.selectors import filter_transactions, transaction_totals
from apps.ledger.services import (
    TransactionData,
    create_account,
    create_transaction,
    ensure_default_account,
    set_account_archived,
)
from apps.taxonomy.models import Category, Tag

TODAY = datetime.date(2026, 10, 15)


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(timezone, "localdate", lambda *a, **k: TODAY)


class TestPeriods:
    @pytest.mark.parametrize(
        ("preset", "expected"),
        [
            ("mes", (datetime.date(2026, 10, 1), datetime.date(2026, 10, 31))),
            ("mes-anterior", (datetime.date(2026, 9, 1), datetime.date(2026, 9, 30))),
            ("30d", (datetime.date(2026, 9, 16), TODAY)),
            ("ano", (datetime.date(2026, 1, 1), datetime.date(2026, 12, 31))),
            ("tudo", (None, None)),
            ("invalido", (None, None)),
        ],
    )
    def test_period_range(self, preset: str, expected: tuple[Any, Any]) -> None:
        assert period_range(preset, TODAY) == expected

    def test_previous_month_across_year(self) -> None:
        assert period_range("mes-anterior", datetime.date(2026, 1, 10)) == (
            datetime.date(2025, 12, 1),
            datetime.date(2025, 12, 31),
        )

    def test_to_query_roundtrip_keys(self) -> None:
        f = TransactionFilter(
            date_from=datetime.date(2026, 10, 1), kinds=("expense",), search="pizza"
        )
        assert f.to_query() == "periodo=personalizado&de=2026-10-01&tipo=expense&q=pizza"
        assert TransactionFilter().to_query() == "periodo=tudo"


@pytest.fixture
def world(user: Any) -> dict[str, Any]:
    """Cenário: corrente, caixinha e cartão; despesas, receita e transferência em 2 meses."""
    checking = ensure_default_account(user)
    checking.opening_date = datetime.date(2026, 1, 1)
    checking.save()
    savings = create_account(user, name="Caixinha", kind=AccountKind.SAVINGS)
    card = create_account(user, name="Cartão", kind=AccountKind.CREDIT_CARD)
    cats = {c.name: c for c in Category.objects.for_user(user)}

    def add(kind: str, cents: int, day: datetime.date, **kw: Any) -> Transaction:
        return create_transaction(
            user, TransactionData(kind=kind, amount_cents=cents, date=day, **kw)
        )

    oct_, sep = datetime.date(2026, 10, 5), datetime.date(2026, 9, 20)
    return {
        "checking": checking,
        "savings": savings,
        "card": card,
        "salary": add("income", 500_000, oct_, account=checking, category=cats["Salário"]),
        "pizza": add(
            "expense",
            6_000,
            oct_,
            account=card,
            category=cats["Alimentação"],
            tags=["Delivery", "Pizza"],
            description="Pizza de sexta",
            payment_method="credit",
        ),
        "uber": add(
            "expense", 2_500, oct_, account=checking, category=cats["Transporte"], tags=["Uber"]
        ),
        "transfer": add("transfer", 100_000, oct_, account=checking, destination_account=savings),
        "september": add("expense", 9_900, sep, account=checking, category=cats["Lazer"]),
    }


def _ids(qs: Any) -> set[Any]:
    return {tx.pk for tx in qs}


@pytest.mark.django_db
class TestFilterSelector:
    def test_period_and_totals_exclude_transfers(self, user: Any, world: dict[str, Any]) -> None:
        f = TransactionFilter(date_from=datetime.date(2026, 10, 1), date_to=TODAY)
        qs = filter_transactions(user, f)
        assert world["september"].pk not in _ids(qs)
        totals = transaction_totals(qs)
        assert (totals.income_cents, totals.expense_cents, totals.count) == (500_000, 8_500, 4)
        assert totals.net_cents == 491_500

    def test_tags_or_within_dimension_without_duplicates(
        self, user: Any, world: dict[str, Any]
    ) -> None:
        tags = Tag.objects.for_user(user)
        ids = tuple(
            tags.filter(normalized_name__in=["delivery", "pizza", "uber"]).values_list(
                "pk", flat=True
            )
        )
        qs = filter_transactions(user, TransactionFilter(tag_ids=ids))
        assert _ids(qs) == {world["pizza"].pk, world["uber"].pk}
        assert transaction_totals(qs).expense_cents == 8_500  # pizza contada uma vez só

    def test_dimensions_combine_with_and(self, user: Any, world: dict[str, Any]) -> None:
        f = TransactionFilter(
            kinds=("expense",),
            account_ids=(world["checking"].pk,),
            date_from=datetime.date(2026, 10, 1),
        )
        assert _ids(filter_transactions(user, f)) == {world["uber"].pk}

    def test_account_matches_transfer_destination(self, user: Any, world: dict[str, Any]) -> None:
        f = TransactionFilter(account_ids=(world["savings"].pk,))
        assert _ids(filter_transactions(user, f)) == {world["transfer"].pk}

    def test_category_payment_and_search(self, user: Any, world: dict[str, Any]) -> None:
        food = world["pizza"].category
        assert _ids(filter_transactions(user, TransactionFilter(category_ids=(food.pk,)))) == {
            world["pizza"].pk
        }
        assert _ids(filter_transactions(user, TransactionFilter(payment_methods=("credit",)))) == {
            world["pizza"].pk
        }
        assert _ids(filter_transactions(user, TransactionFilter(search="SEXTA"))) == {
            world["pizza"].pk
        }

    def test_scoped_to_user(self, user: Any, other_user: Any, world: dict[str, Any]) -> None:
        assert not filter_transactions(other_user, TransactionFilter()).exists()


@pytest.mark.django_db
class TestListView:
    @pytest.fixture
    def logged(self, client: Client, user: Any) -> Client:
        client.force_login(user)
        return client

    def test_default_is_current_month(self, logged: Client, world: dict[str, Any]) -> None:
        html = logged.get(reverse("ledger:transaction_list")).content.decode()
        assert "Pizza de sexta" in html
        assert "01/10/2026 a 31/10/2026" in html
        assert "4 movimentações" in html
        assert "+R$\xa05.000,00" in html  # receitas
        assert "-R$\xa085,00" in html  # despesas

    def test_query_filters_and_active_chips(
        self, logged: Client, user: Any, world: dict[str, Any]
    ) -> None:
        food = world["pizza"].category
        url = reverse("ledger:transaction_list") + f"?periodo=tudo&categoria={food.pk}&tipo=expense"
        response = logged.get(url)
        html = response.content.decode()
        assert "1 movimentação" in html
        assert "Remover filtro Alimentação" in html
        assert "Remover filtro Despesa" in html
        assert "Uber" not in html.split("Lista de movimentações")[1]

    def test_invalid_and_foreign_ids_are_ignored(
        self, logged: Client, other_user: Any, world: dict[str, Any]
    ) -> None:
        theirs = Category.objects.for_user(other_user).first()
        assert theirs is not None
        url = reverse("ledger:transaction_list") + f"?periodo=tudo&categoria={theirs.pk}&de=xx"
        response = logged.get(url)
        assert response.status_code == 200
        assert "5 movimentações" in response.content.decode()

    def test_custom_period(self, logged: Client, world: dict[str, Any]) -> None:
        url = reverse("ledger:transaction_list") + "?de=2026-09-01&ate=2026-09-30"
        html = logged.get(url).content.decode()
        assert "01/09/2026 a 30/09/2026" in html
        assert "1 movimentação" in html

    def test_pagination_keeps_filters(
        self, logged: Client, user: Any, world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("apps.ledger.views.PAGE_SIZE", 2)
        url = reverse("ledger:transaction_list") + "?periodo=tudo&tipo=expense"
        html = logged.get(url).content.decode().replace("&amp;", "&")
        assert "Página 1 de 2" in html
        assert "?periodo=tudo&tipo=expense&pagina=2" in html


@pytest.mark.django_db
class TestEditAndDelete:
    @pytest.fixture
    def logged(self, client: Client, user: Any) -> Client:
        client.force_login(user)
        return client

    def test_edit_form_is_prefilled(self, logged: Client, world: dict[str, Any]) -> None:
        tx = world["pizza"]
        html = logged.get(
            reverse("ledger:transaction_update", args=[tx.pk]), headers={"X-Moneta-Fragment": "1"}
        ).content.decode()
        assert "Editar movimentação" in html
        assert 'value="60,00"' in html
        assert "Delivery, Pizza" in html or "Pizza, Delivery" in html
        assert "Salvar e adicionar outra" not in html
        assert "Confirmar exclusão" in html

    def test_update(self, logged: Client, user: Any, world: dict[str, Any]) -> None:
        tx = world["uber"]
        response = logged.post(
            reverse("ledger:transaction_update", args=[tx.pk]),
            {
                "kind": "expense",
                "amount": "30,00",
                "category": tx.category_id,
                "account": tx.account_id,
                "date": tx.date.isoformat(),
                "tags": "Uber, Trabalho",
                "next": "/movimentacoes/?periodo=tudo",
            },
        )
        assert response.status_code == 302
        assert response["Location"] == "/movimentacoes/?periodo=tudo"
        tx.refresh_from_db()
        assert tx.amount_cents == 3_000
        assert {t.name for t in tx.tags.all()} == {"Uber", "Trabalho"}

    def test_unsafe_next_is_ignored(self, logged: Client, world: dict[str, Any]) -> None:
        tx = world["uber"]
        response = logged.post(
            reverse("ledger:transaction_delete", args=[tx.pk]), {"next": "https://evil.example/"}
        )
        assert response["Location"] == reverse("ledger:transaction_list")

    def test_edit_keeps_archived_account(self, logged: Client, world: dict[str, Any]) -> None:
        tx = world["uber"]
        set_account_archived(world["checking"], archived=True)
        response = logged.post(
            reverse("ledger:transaction_update", args=[tx.pk]),
            {
                "kind": "expense",
                "amount": "25,00",
                "category": tx.category_id,
                "account": tx.account_id,
                "date": tx.date.isoformat(),
            },
            headers={"X-Moneta-Fragment": "1"},
        )
        assert response.status_code == 204

    def test_delete(self, logged: Client, user: Any, world: dict[str, Any]) -> None:
        tx = world["uber"]
        response = logged.post(
            reverse("ledger:transaction_delete", args=[tx.pk]), headers={"X-Moneta-Fragment": "1"}
        )
        assert response.status_code == 204
        assert not Transaction.objects.filter(pk=tx.pk).exists()

    def test_delete_requires_post(self, logged: Client, world: dict[str, Any]) -> None:
        url = reverse("ledger:transaction_delete", args=[world["uber"].pk])
        assert logged.get(url).status_code == 405

    @pytest.mark.parametrize("name", ["ledger:transaction_update", "ledger:transaction_delete"])
    def test_other_users_transactions_are_404(
        self, client: Client, other_user: Any, world: dict[str, Any], name: str
    ) -> None:
        client.force_login(other_user)
        url = reverse(name, args=[world["uber"].pk])
        assert client.get(url).status_code in {404, 405}
        assert client.post(url, {"kind": "expense"}).status_code == 404
        assert Transaction.objects.filter(pk=world["uber"].pk).exists()

    def test_requires_login(self, client: Client, world: dict[str, Any]) -> None:
        url = reverse("ledger:transaction_update", args=[world["uber"].pk])
        assert client.get(url).status_code == 302
