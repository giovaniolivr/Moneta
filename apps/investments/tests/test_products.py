"""Produtos de investimento: regras, modelos prontos, estimativa anual, conta com produto."""

import datetime
from decimal import Decimal
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.investments.domain.products import (
    Indexer,
    MissingRateError,
    ProductSpec,
    Taxation,
)
from apps.investments.domain.rates import CdiPercent, Poupanca, Prefixed
from apps.investments.models import InvestmentProduct, Series
from apps.investments.selectors import product_rows
from apps.investments.services import ProductData, create_product, set_rate_override
from apps.ledger.models import Account, AccountKind
from apps.ledger.services import create_account, ensure_default_account, update_account

D = datetime.date
TODAY = D(2026, 10, 14)
FRAGMENT = {"X-Moneta-Fragment": "1"}
CDI, SELIC = Decimal("0.1365"), Decimal("0.1375")


class TestSpec:
    def test_yield_by_indexer(self) -> None:
        cdi = ProductSpec(Indexer.CDI, Decimal(110), Taxation.TAXED)
        assert cdi.yield_for(cdi=CDI, selic=SELIC) == CdiPercent(CDI, Decimal(110))
        selic = ProductSpec(Indexer.SELIC, Decimal(100), Taxation.TAXED)
        assert selic.yield_for(cdi=CDI, selic=SELIC) == CdiPercent(SELIC, Decimal(100))
        pre = ProductSpec(Indexer.PREFIXED, Decimal("12.5"), Taxation.TAXED)
        assert pre.yield_for(cdi=None, selic=None) == Prefixed(Decimal("0.125"))
        poup = ProductSpec(Indexer.POUPANCA, None, Taxation.EXEMPT)
        assert poup.yield_for(cdi=None, selic=SELIC) == Poupanca(SELIC)
        with pytest.raises(MissingRateError, match="CDI"):
            cdi.yield_for(cdi=None, selic=SELIC)

    def test_one_year_net_of_tax(self) -> None:
        taxed = ProductSpec(Indexer.CDI, Decimal(100), Taxation.TAXED)
        gross, net = taxed.one_year(cdi=CDI, selic=SELIC, start=TODAY)
        assert net == gross * Decimal("0.825")  # 1 ano (365 dias) → IR de 17,5%
        exempt = ProductSpec(Indexer.CDI, Decimal(100), Taxation.IR_EXEMPT)
        assert exempt.one_year(cdi=CDI, selic=SELIC, start=TODAY) == (gross, gross)


@pytest.mark.django_db
class TestModel:
    def test_rate_required_except_poupanca(self, user: Any) -> None:
        with pytest.raises(ValidationError, match="taxa que o banco paga"):
            create_product(user, ProductData("CDB", "cdi", None, "taxed", daily_liquidity=False))
        poup = create_product(
            user, ProductData("Poupança", "poupanca", Decimal(5), "taxed", daily_liquidity=True)
        )
        assert (poup.rate_percent, poup.taxation) == (None, "exempt")  # regra oficial

    def test_unique_name_per_user(self, user: Any, other_user: Any) -> None:
        data = ProductData("Caixinha", "cdi", Decimal(100), "taxed", daily_liquidity=True)
        create_product(user, data)
        with pytest.raises(ValidationError, match="Já existe"):
            create_product(user, ProductData("CAIXINHA", "cdi", Decimal(100), "taxed", True))
        create_product(other_user, data)  # outro usuário pode ter o mesmo nome

    def test_account_product_rules(self, user: Any, other_user: Any) -> None:
        mine = create_product(user, ProductData("Caixinha", "cdi", Decimal(100), "taxed", True))
        theirs = create_product(
            other_user, ProductData("Caixinha", "cdi", Decimal(100), "taxed", True)
        )
        savings = create_account(user, name="Reserva", kind=AccountKind.SAVINGS, product=mine)
        assert savings.product == mine
        with pytest.raises(ValidationError, match="Produto inválido"):
            update_account(savings, product=theirs)
        savings.refresh_from_db()
        card = create_account(user, name="Cartão", kind=AccountKind.CREDIT_CARD, product=mine)
        assert card.product is None  # cartão não rende
        update_account(savings, name="Reserva 2")  # sem `product`: mantém
        savings.refresh_from_db()
        assert savings.product == mine


@pytest.mark.django_db
class TestRowsAndViews:
    @pytest.fixture(autouse=True)
    def _fixed_today(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(timezone, "localdate", lambda *a, **k: TODAY)

    def test_rows_need_market_rate(self, user: Any) -> None:
        create_product(user, ProductData("Caixinha", "cdi", Decimal(100), "taxed", True))
        [row] = product_rows(user, TODAY)
        assert (row.missing, row.gross_year) == ("CDI", None)
        set_rate_override(user, Series.CDI, Decimal("13.65"))
        [row] = product_rows(user, TODAY)
        assert row.missing == ""
        assert row.gross_year is not None
        assert Decimal("0.13") < row.gross_year < Decimal("0.14")

    def test_preset_prefills_and_blank_rate_for_bank_dependent(
        self, client: Client, user: Any
    ) -> None:
        client.force_login(user)
        url = reverse("investments:product_create")
        caixinha = client.get(url + "?modelo=caixinha", headers=FRAGMENT).content.decode()
        assert 'value="Caixinha"' in caixinha
        assert 'value="100"' in caixinha
        turbo = client.get(url + "?modelo=caixinha-turbo", headers=FRAGMENT).content.decode()
        assert 'value="Caixinha Turbo"' in turbo
        assert 'name="rate" value=' not in turbo  # taxa em branco: o banco é quem diz

    def test_create_edit_delete(self, client: Client, user: Any) -> None:
        client.force_login(user)
        response = client.post(
            reverse("investments:product_create"),
            {
                "name": "Caixinha Turbo",
                "indexer": "cdi",
                "rate": "120",
                "taxation": "taxed",
                "daily_liquidity": "on",
            },
            headers=FRAGMENT,
        )
        assert response.status_code == 204
        product = InvestmentProduct.objects.get()
        assert product.rate_label == "120% do CDI"
        missing = client.post(
            reverse("investments:product_update", args=[product.pk]),
            {"name": "Caixinha Turbo", "indexer": "cdi", "rate": "", "taxation": "taxed"},
            headers=FRAGMENT,
        ).content.decode()
        assert "Informe a taxa que o banco paga" in missing
        page = client.get(reverse("investments:index")).content.decode()
        assert "falta a taxa CDI" in page
        client.post(reverse("investments:product_delete", args=[product.pk]), headers=FRAGMENT)
        assert not InvestmentProduct.objects.exists()

    def test_account_form_offers_own_products(
        self, client: Client, user: Any, other_user: Any
    ) -> None:
        product = create_product(user, ProductData("Caixinha", "cdi", Decimal(100), "taxed", True))
        theirs = create_product(
            other_user, ProductData("Outro", "cdi", Decimal(100), "taxed", True)
        )
        client.force_login(user)
        form = client.get(reverse("ledger:account_create"), headers=FRAGMENT).content.decode()
        assert str(product.pk) in form
        assert str(theirs.pk) not in form
        client.post(
            reverse("ledger:account_create"),
            {
                "name": "Reserva",
                "kind": "savings",
                "opening_balance": "",
                "opening_date": "2026-10-01",
                "product": str(product.pk),
            },
            headers=FRAGMENT,
        )
        assert Account.objects.get(name="Reserva").product == product

    @pytest.mark.parametrize("name", ["investments:product_update", "investments:product_delete"])
    def test_other_users_products_are_404(
        self, client: Client, user: Any, other_user: Any, name: str
    ) -> None:
        product = create_product(user, ProductData("Caixinha", "cdi", Decimal(100), "taxed", True))
        ensure_default_account(other_user)
        client.force_login(other_user)
        assert client.post(reverse(name, args=[product.pk])).status_code == 404
        assert InvestmentProduct.objects.filter(pk=product.pk).exists()
