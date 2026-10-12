"""Lista de desejos: loja pelo link, melhor preço agora, histórico, telas e escopo."""

import datetime
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.shopping.domain.prices import Observation, store_from_url, summarize
from apps.shopping.models import PriceObservation, WishItem
from apps.shopping.selectors import best_price_series
from apps.shopping.services import ObservationData, add_observation, create_item

D = datetime.date
TODAY = D(2026, 10, 14)
FRAGMENT = {"X-Moneta-Fragment": "1"}


@pytest.mark.parametrize(
    ("url", "store"),
    [
        ("https://www.kabum.com.br/produto/123/mouse", "KaBuM!"),
        ("https://www.pichau.com.br/x", "Pichau"),
        ("https://www.terabyteshop.com.br/produto/9", "Terabyte"),
        ("https://produto.mercadolivre.com.br/MLB-1", "Mercado Livre"),
        ("https://www.amazon.com.br/dp/B0", "Amazon"),
        ("https://loja.exemplo.com.br/p", "loja.exemplo.com.br"),
        ("https://kabum.com.br.golpe.com/p", "kabum.com.br.golpe.com"),  # não engana
    ],
)
def test_store_from_url(url: str, store: str) -> None:
    assert store_from_url(url) == store


class TestSummary:
    def test_best_now_uses_latest_per_store_with_shipping(self) -> None:
        obs = [
            Observation("KaBuM!", D(2026, 10, 1), 20_000),
            Observation("KaBuM!", D(2026, 10, 10), 22_000),  # subiu: o atual é este
            Observation("Pichau", D(2026, 10, 12), 19_000, shipping_cents=2_500),
            Observation("Terabyte", D(2026, 8, 1), 15_000),  # velho demais para "agora"
        ]
        s = summarize(obs, today=TODAY, target_cents=21_500)
        assert s.best_now == obs[2]  # 19.000 + 2.500 = 21.500 < 22.000
        assert [o.store for o in s.stores_now] == ["Pichau", "KaBuM!"]
        assert s.lowest_ever == obs[3]
        assert s.below_target
        assert not s.is_lowest_ever

    def test_empty(self) -> None:
        s = summarize([], today=TODAY, target_cents=None)
        assert (s.best_now, s.lowest_ever, s.below_target) == (None, None, False)


@pytest.mark.django_db
class TestServices:
    @pytest.fixture(autouse=True)
    def _fixed_today(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(timezone, "localdate", lambda *a, **k: TODAY)

    def test_store_comes_from_link_and_rules(self, user: Any) -> None:
        item = create_item(user, name="Teclado", target_cents=None)
        obs = add_observation(
            item, ObservationData(price_cents=30_000, url="https://www.pichau.com.br/teclado")
        )
        assert (obs.store, obs.observed_on, obs.owner_id) == ("Pichau", TODAY, user.pk)
        with pytest.raises(ValidationError, match="Informe a loja"):
            add_observation(item, ObservationData(price_cents=1))
        with pytest.raises(ValidationError, match="futura"):
            add_observation(
                item, ObservationData(price_cents=1, store="X", observed_on=D(2026, 12, 1))
            )
        with pytest.raises(ValidationError):
            add_observation(item, ObservationData(price_cents=0, store="X"))

    def test_best_price_series(self, user: Any) -> None:
        item = create_item(user, name="Monitor", target_cents=None)
        for day, store, cents in [
            (D(2026, 10, 1), "KaBuM!", 100_000),
            (D(2026, 10, 3), "Pichau", 95_000),
            (D(2026, 10, 5), "Pichau", 105_000),  # Pichau subiu: KaBuM! volta a ser o menor
        ]:
            add_observation(item, ObservationData(price_cents=cents, store=store, observed_on=day))
        assert best_price_series(item) == [
            (D(2026, 10, 1), 100_000),
            (D(2026, 10, 3), 95_000),
            (D(2026, 10, 5), 100_000),
        ]


@pytest.mark.django_db
class TestViews:
    @pytest.fixture(autouse=True)
    def _fixed_today(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(timezone, "localdate", lambda *a, **k: TODAY)

    def test_flow(self, client: Client, user: Any) -> None:
        client.force_login(user)
        response = client.post(
            reverse("shopping:item_create"),
            {"name": "Headset", "target": "250,00", "notes": ""},
            headers=FRAGMENT,
        )
        assert response.status_code == 204
        item = WishItem.objects.get()
        response = client.post(
            reverse("shopping:observation_create", args=[item.pk]),
            {
                "url": "https://www.kabum.com.br/produto/1",
                "store": "",
                "price": "239,90",
                "shipping": "",
                "installments": "10",
                "observed_on": "2026-10-14",
            },
            headers=FRAGMENT,
        )
        assert response.status_code == 204
        page = client.get(reverse("shopping:index")).content.decode()
        assert "Headset" in page
        assert "no alvo" in page
        assert "melhor agora: KaBuM!" in page
        detail = client.get(reverse("shopping:item_detail", args=[item.pk])).content.decode()
        assert "10x sem juros" in detail
        assert 'rel="noopener noreferrer"' in detail
        obs = PriceObservation.objects.get()
        client.post(reverse("shopping:observation_delete", args=[obs.pk]), headers=FRAGMENT)
        assert not PriceObservation.objects.exists()
        client.post(reverse("shopping:item_toggle_done", args=[item.pk]), headers=FRAGMENT)
        item.refresh_from_db()
        assert item.is_done

    def test_rejects_non_http_links(self, client: Client, user: Any) -> None:
        item = create_item(user, name="X", target_cents=None)
        client.force_login(user)
        html = client.post(
            reverse("shopping:observation_create", args=[item.pk]),
            {"url": "javascript:alert(1)", "price": "10,00", "observed_on": "2026-10-14"},
            headers=FRAGMENT,
        ).content.decode()
        assert "Informe uma URL válida" in html
        assert not PriceObservation.objects.exists()

    @pytest.mark.parametrize(
        "name",
        [
            "shopping:item_detail",
            "shopping:item_update",
            "shopping:item_toggle_done",
            "shopping:item_delete",
            "shopping:observation_create",
        ],
    )
    def test_other_users_items_are_404(
        self, client: Client, user: Any, other_user: Any, name: str
    ) -> None:
        item = create_item(user, name="Meu", target_cents=None)
        client.force_login(other_user)
        assert client.post(reverse(name, args=[item.pk]), {"price": "1,00"}).status_code == 404
        assert WishItem.objects.filter(pk=item.pk, is_done=False).exists()

    def test_other_users_observations_are_404(
        self, client: Client, user: Any, other_user: Any
    ) -> None:
        item = create_item(user, name="Meu", target_cents=None)
        obs = add_observation(item, ObservationData(price_cents=100, store="X"))
        client.force_login(other_user)
        assert client.post(reverse("shopping:observation_delete", args=[obs.pk])).status_code == 404
        assert PriceObservation.objects.filter(pk=obs.pk).exists()
        assert "Meu" not in client.get(reverse("shopping:index")).content.decode()
