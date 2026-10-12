import datetime
import json
import re
from decimal import Decimal
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.dashboard.templatetags.dashboard_ui import pct
from apps.ledger.services import TransactionData, create_transaction, ensure_default_account
from apps.taxonomy.models import Category

pytestmark = pytest.mark.django_db
TODAY = datetime.date(2026, 10, 10)


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(timezone, "localdate", lambda *a, **k: TODAY)


@pytest.fixture
def logged(client: Client, user: Any) -> Client:
    account = ensure_default_account(user)
    account.opening_date = datetime.date(2026, 1, 1)
    account.save()
    cats = {c.name: c for c in Category.objects.for_user(user)}
    for kind, cents, day, name in [
        ("income", 500_000, datetime.date(2026, 10, 5), "Salário"),
        ("expense", 12_000, datetime.date(2026, 10, 2), "Alimentação"),
        ("expense", 30_000, datetime.date(2026, 9, 3), "Lazer"),
    ]:
        create_transaction(
            user,
            TransactionData(
                kind=kind,
                amount_cents=cents,
                date=day,
                account=account,
                category=cats[name],
                tags=["Mercado"] if name == "Alimentação" else [],
            ),
        )
    client.force_login(user)
    return client


def test_renders_kpis_charts_and_tables(logged: Client) -> None:
    html = logged.get(reverse("home")).content.decode()
    assert "Saldo total hoje" in html
    assert "R$\xa04.880,00" in html  # 5.000 - 120 (outubro) - 300 (setembro)
    assert 'data-chart="columns"' in html
    assert 'id="data-monthly"' in html
    assert "Ver tabela" in html  # todo gráfico tem tabela equivalente
    assert "comparado com 01/09 a 10/09 (mesmos dias)" in html


def test_chart_links_point_to_transaction_list(logged: Client) -> None:
    html = logged.get(reverse("home")).content.decode()
    raw = re.search(r'<script id="data-monthly" type="application/json">(.*?)</script>', html)
    assert raw is not None
    links = json.loads(raw.group(1))["links"]
    assert all(link.startswith("/movimentacoes/?") for link in links)


def test_chart_payload_is_valid_json(logged: Client) -> None:
    html = logged.get(reverse("home")).content.decode()
    raw = re.search(r'<script id="data-monthly" type="application/json">(.*?)</script>', html)
    assert raw is not None
    data = json.loads(raw.group(1))
    assert data["series"][1]["values"][-1] == 12_000


def test_category_bars_link_to_filtered_list(logged: Client, user: Any) -> None:
    food = Category.objects.for_user(user).get(name="Alimentação")
    html = logged.get(reverse("home")).content.decode()
    assert f"categoria={food.pk}" in html
    link = re.search(r'href="(/movimentacoes/\?[^"]*categoria=[^"]*)"', html)
    assert link is not None
    listing = logged.get(link.group(1).replace("&amp;", "&")).content.decode()
    assert "1 movimentação" in listing


@pytest.mark.parametrize("period", ["mes", "mes-anterior", "3m", "12m", "ano", "invalido"])
def test_all_periods_render(logged: Client, period: str) -> None:
    response = logged.get(reverse("home") + f"?periodo={period}")
    assert response.status_code == 200


def test_previous_month_period(logged: Client) -> None:
    html = logged.get(reverse("home") + "?periodo=mes-anterior").content.decode()
    assert "Ritmo de gastos" in html
    assert "R$\xa0300,00" in html


def test_new_user_sees_empty_state(client: Client, other_user: Any) -> None:
    client.force_login(other_user)
    html = client.get(reverse("home")).content.decode()
    assert "Nenhuma receita ou despesa neste período" in html
    assert "Sem despesas no período." in html


def test_does_not_leak_other_users_data(logged: Client, client: Client, other_user: Any) -> None:
    client.force_login(other_user)
    html = client.get(reverse("home")).content.decode()
    assert "Mercado" not in html
    assert "5.000,00" not in html


@pytest.mark.parametrize(
    ("value", "expected"),
    [(Decimal("18.0"), "18%"), (Decimal("33.3"), "33,3%"), (Decimal("-5.5"), "-5,5%"), (None, "—")],
)
def test_pct_filter(value: Decimal | None, expected: str) -> None:
    assert pct(value) == expected


def test_renders_insights_with_evidence_and_pending(logged: Client) -> None:
    html = logged.get(reverse("home")).content.decode()
    assert 'id="insights-title"' in html
    assert "Em outubro até o dia 10, você gastou" in html
    assert "Por quê?" in html  # evidência numérica sempre visível
    assert "Ver movimentações" in html
    # Uso começou em setembro: as regras que dependem de histórico declaram a limitação.
    assert "Ainda sem dados suficientes" in html
    assert "(há 0)" in html


def test_no_insights_without_data(client: Client, other_user: Any) -> None:
    client.force_login(other_user)
    html = client.get(reverse("home")).content.decode()
    assert 'id="insights-title"' not in html
