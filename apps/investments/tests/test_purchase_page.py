"""Tela de estratégias de compra: ofertas → recomendação com evidência."""

import datetime
from decimal import Decimal
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.investments.domain.purchase import compare
from apps.investments.domain.rates import Prefixed
from apps.investments.domain.taxes import EXEMPT
from apps.investments.models import Series
from apps.investments.selectors import explain, offered_strategies
from apps.investments.services import ProductData, create_product, set_rate_override

pytestmark = pytest.mark.django_db
D = datetime.date
TODAY = D(2026, 10, 14)
URL = "investments:purchase"


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(timezone, "localdate", lambda *a, **k: TODAY)


def test_strategies_from_offers() -> None:
    data = {
        "valor": 300_000,
        "desconto": Decimal(5),
        "sj_parcelas": 10,
        "sj_no_ato": True,
        "cj_parcelas": 12,
        "cj_valor": 29_000,
        "en_entrada": 50_000,
        "en_parcelas": 5,
        "en_valor": 52_000,
    }
    cash, free, interest, down = offered_strategies(data, TODAY)
    assert (cash.label, cash.total_cents) == ("À vista com 5% de desconto", 285_000)
    assert free.payments[0].day == TODAY  # 1ª parcela no ato
    assert free.total_cents == 300_000
    assert interest.total_cents == 348_000
    assert down.payments[0].cents == 50_000
    assert down.total_cents == 310_000


def test_page_recommends_with_evidence(client: Client, user: Any) -> None:
    set_rate_override(user, Series.CDI, Decimal("13.65"))
    caixinha = create_product(user, ProductData("Caixinha", "cdi", Decimal(100), "taxed", True))
    client.force_login(user)
    html = client.get(
        reverse(URL),
        {
            "valor": "3.000,00",
            "produto": str(caixinha.pk),
            "desconto": "0",
            "sj_parcelas": "10",
            "cj_parcelas": "12",
            "cj_valor": "290,00",
        },
    ).content.decode()
    assert "10x sem juros é a opção que menos pesa" in html
    assert "o dinheiro continua rendendo (Caixinha (100% do CDI))" in html
    assert "12x de R$\xa0290,00" in html  # juros dessa oferta: na tabela
    assert html.count("badge-best") == 1
    assert "à vista sem desconto custa exatamente o preço" in html


def test_without_yield_spreads_payment(client: Client, user: Any) -> None:
    client.force_login(user)
    html = client.get(
        reverse(URL), {"valor": "1.000,00", "produto": "nenhum", "sj_parcelas": "5"}
    ).content.decode()
    assert "o custo é o mesmo, mas o desembolso se espalha pelos meses" in html


def test_validation(client: Client, user: Any) -> None:
    client.force_login(user)
    html = client.get(
        reverse(URL), {"valor": "1.000,00", "produto": "nenhum", "cj_parcelas": "12"}
    ).content.decode()
    assert "Parcelado com juros: informe o número de parcelas e o valor" in html
    bad = client.get(
        reverse(URL), {"valor": "1.000,00", "produto": "nenhum", "desconto": "150"}
    ).content.decode()
    assert "Use um desconto entre 0 e 99%" in bad


def test_missing_rate(client: Client, user: Any) -> None:
    client.force_login(user)
    html = client.get(
        reverse(URL), {"valor": "1.000,00", "produto": "ref-cdi", "sj_parcelas": "3"}
    ).content.decode()
    assert "Falta a taxa CDI" in html


def test_other_users_product_is_not_a_valid_choice(
    client: Client, user: Any, other_user: Any
) -> None:
    theirs = create_product(other_user, ProductData("Secreto", "cdi", Decimal(200), "taxed", True))
    client.force_login(user)
    html = client.get(
        reverse(URL), {"valor": "1.000,00", "produto": str(theirs.pk), "sj_parcelas": "3"}
    ).content.decode()
    assert "Recomendação" not in html
    assert "Secreto" not in html


def test_explain_single_strategy() -> None:
    strategies = offered_strategies({"valor": 10_000, "desconto": Decimal(0)}, TODAY)
    comparison = compare(
        strategies, yield_=Prefixed(Decimal(0)), taxes=EXEMPT, start=TODAY, cash_price_cents=10_000
    )
    assert explain(comparison, "parado") == "À vista custa R$\xa0100,00 em dinheiro de hoje."
