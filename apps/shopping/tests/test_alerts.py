"""Alertas de preço na dashboard e atalhos da lista para estratégias de compra e metas."""

import datetime
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.analytics.domain.insights import PriceRule, PriceSignal
from apps.analytics.tests.test_insights import ctx
from apps.ledger.services import TransactionData, create_transaction, ensure_default_account
from apps.shopping.domain.prices import Observation, previous_low
from apps.shopping.selectors import price_alerts
from apps.shopping.services import ObservationData, add_observation, create_item, set_item_done
from apps.taxonomy.models import Category

D = datetime.date
TODAY = D(2026, 10, 14)


def test_previous_low_window() -> None:
    obs = [
        Observation("A", D(2026, 7, 1), 9_000),  # fora da janela de 60 dias
        Observation("A", D(2026, 9, 1), 12_000),
        Observation("B", D(2026, 10, 1), 11_000),
        Observation("B", D(2026, 10, 12), 10_000),
    ]
    assert previous_low(obs, best=obs[-1], today=TODAY) == 11_000
    assert previous_low(obs[-1:], best=obs[-1], today=TODAY) is None


def test_price_rule_messages() -> None:
    signals = [
        PriceSignal("Headset", "/desejos/1/", "KaBuM!", 44_990, "target", target_cents=45_000),
        PriceSignal("Monitor", "/desejos/2/", "Pichau", 99_000, "low", previous_low_cents=105_000),
    ]
    target, low = PriceRule().evaluate(ctx(prices=signals))
    assert target.message == "Headset: R$\xa0449,90 na KaBuM!, dentro do seu alvo de R$\xa0450,00."
    assert low.message == (
        "Monitor: R$\xa0990,00 na Pichau, o menor preço dos últimos 60 dias "
        "(antes: R$\xa01.050,00)."
    )
    assert (target.severity, target.url) == ("positive", "/desejos/1/")


@pytest.mark.django_db
class TestWithData:
    @pytest.fixture(autouse=True)
    def _fixed_today(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(timezone, "localdate", lambda *a, **k: TODAY)

    def _item(self, user: Any, name: str, target: int | None, prices: list[tuple[int, int]]) -> Any:
        item = create_item(user, name=name, target_cents=target)
        for days_ago, cents in prices:
            add_observation(
                item,
                ObservationData(
                    price_cents=cents,
                    store="KaBuM!",
                    installments=10,
                    observed_on=TODAY - datetime.timedelta(days=days_ago),
                ),
            )
        return item

    def test_alerts(self, user: Any) -> None:
        on_target = self._item(user, "Headset", 45_000, [(5, 44_990)])
        dropped = self._item(user, "Monitor", None, [(30, 105_000), (2, 99_000)])
        self._item(user, "Cadeira", None, [(30, 90_000), (2, 95_000)])  # subiu: sem alerta
        self._item(user, "Velho", 10, [(45, 5)])  # sem preço recente: sem alerta
        done = self._item(user, "Comprado", 100_000, [(1, 1_000)])
        set_item_done(done, done=True)
        alerts = {a.item.name: a for a in price_alerts(user, TODAY)}
        assert set(alerts) == {"Headset", "Monitor"}
        assert alerts["Headset"].kind == "target"
        assert (alerts["Monitor"].kind, alerts["Monitor"].previous_low_cents) == ("low", 105_000)
        assert alerts["Headset"].item == on_target
        assert alerts["Monitor"].item == dropped

    def test_dashboard_shows_alert_only_on_current_month(self, client: Client, user: Any) -> None:
        item = self._item(user, "Headset", 45_000, [(5, 44_990)])
        food = Category.objects.for_user(user).get(name="Alimentação")
        create_transaction(  # quem ainda não lançou nada vê os primeiros passos
            user,
            TransactionData(
                kind="expense",
                amount_cents=1_000,
                date=TODAY,
                account=ensure_default_account(user),
                category=food,
            ),
        )
        client.force_login(user)
        html = client.get(reverse("home")).content.decode()
        assert "Headset: R$\xa0449,90 na KaBuM!, dentro do seu alvo" in html
        assert reverse("shopping:item_detail", args=[item.pk]) in html
        other = client.get(reverse("home") + "?periodo=3m").content.decode()
        assert "dentro do seu alvo" not in other

    def test_alerts_are_scoped(self, client: Client, user: Any, other_user: Any) -> None:
        self._item(user, "Headset", 45_000, [(5, 44_990)])
        assert price_alerts(other_user, TODAY) == []

    def test_item_shortcuts(self, client: Client, user: Any) -> None:
        item = self._item(user, "Headset", 45_000, [(5, 44_990)])
        client.force_login(user)
        html = client.get(reverse("shopping:item_detail", args=[item.pk])).content.decode()
        assert "valor=449%2C90" in html
        assert "sj_parcelas=10" in html
        assert "nome=Headset&amp;alvo=450.00" in html
        compare = client.get(
            reverse("investments:purchase"),
            {"valor": "449,90", "produto": "nenhum", "sj_parcelas": "10"},
        ).content.decode()
        assert "Recomendação" in compare  # o link leva a uma comparação válida

    def test_no_shortcuts_without_price_or_target(self, client: Client, user: Any) -> None:
        item = create_item(user, name="Ideia", target_cents=None)
        client.force_login(user)
        html = client.get(reverse("shopping:item_detail", args=[item.pk])).content.decode()
        assert "Comparar formas de pagar" not in html
        assert "Criar meta" not in html
