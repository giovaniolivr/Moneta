"""Orçamentos: ritmo pelo padrão do usuário (não pela reta), situações, telas, análise, escopo."""

import datetime
from decimal import Decimal
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.analytics.dashboard import build_dashboard
from apps.analytics.domain.insights import BudgetRule, BudgetSignal
from apps.ledger.services import TransactionData, create_transaction, ensure_default_account
from apps.planning.domain.budget import progress, typical_fraction
from apps.planning.models import Budget
from apps.planning.selectors import budget_rows
from apps.planning.services import create_budget, delete_budget, update_budget
from apps.taxonomy.models import Category

D = datetime.date
TODAY = D(2026, 10, 7)
FRAGMENT = {"X-Moneta-Fragment": "1"}
OCT = {"month_start": D(2026, 10, 1), "month_end": D(2026, 10, 31)}


class TestDomain:
    def test_typical_fraction_needs_two_months(self) -> None:
        assert typical_fraction([150_000, 150_000], [160_000, 160_000]) == Decimal("0.9375")
        assert typical_fraction([100], [200]) is None
        assert typical_fraction([0, 100], [0, 200]) is None  # mês sem gasto não conta

    def test_history_avoids_false_alarm_for_early_fixed_costs(self) -> None:
        # Aluguel no dia 5: no dia 7 já se gastou quase tudo — e isso é normal.
        rent = progress(
            limit_cents=160_000,
            spent_cents=150_000,
            data_until=D(2026, 10, 7),
            fraction=Decimal("0.94"),
            **OCT,
        )
        assert (rent.method, rent.status) == ("history", "ok")
        linear = progress(
            limit_cents=160_000,
            spent_cents=150_000,
            data_until=D(2026, 10, 7),
            fraction=None,
            **OCT,
        )
        assert (linear.method, linear.status) == ("linear", "pace")  # a reta daria alarme falso

    def test_pace_over_and_allowance(self) -> None:
        pace = progress(
            limit_cents=50_000, spent_cents=30_000, data_until=D(2026, 10, 10), fraction=None, **OCT
        )
        assert pace.expected_cents == 16_129  # 10/31 do limite
        assert pace.projected_cents == 93_000
        assert pace.status == "pace"
        assert pace.daily_allowance_cents == 909  # R$ 200 restantes / 22 dias
        over = progress(
            limit_cents=50_000, spent_cents=50_001, data_until=D(2026, 10, 10), fraction=None, **OCT
        )
        assert over.status == "over"
        assert over.daily_allowance_cents is None

    def test_too_early_to_project(self) -> None:
        early = progress(
            limit_cents=50_000, spent_cents=20_000, data_until=D(2026, 10, 3), fraction=None, **OCT
        )
        assert early.projected_cents is None
        assert early.status == "ok"

    def test_closed_month(self) -> None:
        closed = progress(
            limit_cents=50_000, spent_cents=45_000, data_until=D(2026, 11, 2), fraction=None, **OCT
        )
        assert (closed.method, closed.partial, closed.status) == ("closed", False, "ok")
        assert closed.projected_cents == 45_000


def signal(**kw: Any) -> BudgetSignal:
    base: dict[str, Any] = {
        "label": "Lazer",
        "category_id": None,
        "limit_cents": 50_000,
        "spent_cents": 30_000,
        "expected_cents": 16_000,
        "projected_cents": 93_000,
        "status": "pace",
        "method": "linear",
    }
    return BudgetSignal(**(base | kw))


class TestInsightRule:
    def _ctx(self, **kw: Any) -> Any:
        from apps.analytics.tests.test_insights import ctx

        return ctx(**kw)

    def test_pace_and_over_alerts_with_evidence(self) -> None:
        context = self._ctx(budgets=[signal(), signal(status="over", spent_cents=60_000)])
        pace, over = BudgetRule().evaluate(context)
        assert pace.severity == over.severity == "attention"
        assert "no ritmo de passar do limite" in pace.message
        assert "projeção de R$\xa0930,00" in pace.message
        assert "estourado: R$\xa0600,00 de R$\xa0500,00" in over.message
        methods = {e.label: e.value for e in pace.evidence}
        assert str(methods["Como o esperado é calculado"]).startswith("Proporcional aos dias")

    def test_closed_month_all_within_is_positive(self) -> None:
        from apps.analytics.domain.periods import DateRange

        closed = self._ctx(
            current=DateRange(D(2026, 9, 1), D(2026, 9, 30)),
            partial=False,
            budgets=[signal(status="ok", method="closed"), signal(status="ok", method="closed")],
        )
        [insight] = BudgetRule().evaluate(closed)
        assert insight.severity == "positive"
        assert insight.message == "Todos os 2 orçamentos ficaram dentro do limite em setembro."

    def test_only_single_month(self) -> None:
        assert BudgetRule().evaluate(self._ctx(single_month=False, budgets=[signal()])) == []


@pytest.mark.django_db
class TestBudgetRows:
    @pytest.fixture(autouse=True)
    def _fixed_today(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(timezone, "localdate", lambda *a, **k: TODAY)

    @pytest.fixture
    def history(self, user: Any) -> dict[str, Category]:
        account = ensure_default_account(user)
        account.opening_date = D(2026, 1, 1)
        account.save()
        cats = {c.name: c for c in Category.objects.for_user(user)}

        def add(cents: int, day: datetime.date, name: str) -> None:
            create_transaction(
                user,
                TransactionData(
                    kind="expense",
                    amount_cents=cents,
                    date=day,
                    account=account,
                    category=cats[name],
                ),
            )

        add(1_000, D(2026, 6, 1), "Outros")  # uso desde 1º/jun → jul, ago, set completos
        for month in (7, 8, 9, 10):
            add(150_000, D(2026, month, 5), "Moradia")
        for month in (7, 8, 9):
            add(10_000, D(2026, month, 3), "Lazer")
            add(30_000, D(2026, month, 20), "Lazer")
        add(35_000, D(2026, 10, 2), "Lazer")  # gastou cedo o que costuma gastar no mês todo
        return cats

    def test_uses_users_own_pattern(self, user: Any, history: Any) -> None:
        create_budget(user, category=history["Moradia"], amount_cents=160_000)
        create_budget(user, category=history["Lazer"], amount_cents=45_000)
        create_budget(user, category=None, amount_cents=300_000)
        rows = {r.budget.label: r.progress for r in budget_rows(user, TODAY, TODAY)}
        assert next(iter(rows)) == "Todas as despesas"  # o total vem primeiro
        moradia = rows["Moradia"]
        assert (moradia.method, moradia.status) == ("history", "ok")  # aluguel do dia 5 é normal
        lazer = rows["Lazer"]
        assert lazer.fraction == Decimal("0.25")  # até o dia 7 costuma gastar 1/4
        assert lazer.expected_cents == 11_250
        assert (lazer.status, lazer.projected_cents) == ("pace", 140_000)
        assert rows["Todas as despesas"].spent_cents == 185_000

    def test_dashboard_insight(self, user: Any, history: Any) -> None:
        create_budget(user, category=history["Lazer"], amount_cents=45_000)
        client = Client()
        client.force_login(user)
        html = client.get(reverse("home")).content.decode()
        assert "Orçamento de Lazer no ritmo de passar do limite" in html
        # Sem a injeção da view (ex.: API futura), nenhuma regra de orçamento roda.
        dash = build_dashboard(user, "mes", TODAY)
        assert not [i for i in dash.insights if i.rule_id == "orcamento"]

    def test_scoped(self, user: Any, other_user: Any, history: Any) -> None:
        create_budget(user, category=None, amount_cents=300_000)
        assert budget_rows(other_user, TODAY, TODAY) == []


@pytest.mark.django_db
class TestServicesAndViews:
    @pytest.fixture(autouse=True)
    def _fixed_today(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(timezone, "localdate", lambda *a, **k: TODAY)

    def test_rules(self, user: Any, other_user: Any) -> None:
        food = Category.objects.for_user(user).get(name="Alimentação")
        salary = Category.objects.for_user(user).get(name="Salário")
        budget = create_budget(user, category=food, amount_cents=100_000)
        with pytest.raises(ValidationError, match="já tem orçamento"):
            create_budget(user, category=food, amount_cents=1)
        with pytest.raises(ValidationError, match="despesa"):
            create_budget(user, category=salary, amount_cents=1)
        theirs = Category.objects.for_user(other_user).get(name="Alimentação")
        with pytest.raises(ValidationError, match="inválida"):
            create_budget(user, category=theirs, amount_cents=1)
        create_budget(user, category=None, amount_cents=500_000)
        with pytest.raises(ValidationError, match="total do mês"):
            create_budget(user, category=None, amount_cents=1)
        update_budget(budget, amount_cents=120_000)
        delete_budget(budget)
        assert Budget.objects.count() == 1

    def test_create_edit_delete_views(self, client: Client, user: Any) -> None:
        client.force_login(user)
        food = Category.objects.for_user(user).get(name="Alimentação")
        url = reverse("planning:budget_create")
        assert (
            client.post(
                url, {"category": str(food.pk), "amount": "1.500,00"}, headers=FRAGMENT
            ).status_code
            == 204
        )
        form = client.get(url, headers=FRAGMENT).content.decode()
        assert f'value="{food.pk}"' not in form  # categoria com orçamento sai da lista
        budget = Budget.objects.get()
        client.post(
            reverse("planning:budget_update", args=[budget.pk]),
            {"amount": "1.800,00"},
            headers=FRAGMENT,
        )
        budget.refresh_from_db()
        assert budget.amount_cents == 180_000
        page = client.get(reverse("planning:index")).content.decode()
        assert "Orçamentos de outubro" in page
        assert "R$\xa01.800,00" in page
        client.post(reverse("planning:budget_delete", args=[budget.pk]), headers=FRAGMENT)
        assert not Budget.objects.exists()

    @pytest.mark.parametrize("name", ["planning:budget_update", "planning:budget_delete"])
    def test_other_users_budgets_are_404(
        self, client: Client, user: Any, other_user: Any, name: str
    ) -> None:
        budget = create_budget(user, category=None, amount_cents=100_000)
        client.force_login(other_user)
        assert client.post(reverse(name, args=[budget.pk]), {"amount": "1,00"}).status_code == 404
        budget.refresh_from_db()
        assert budget.amount_cents == 100_000
