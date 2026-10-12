"""Taxas de mercado: API do Banco Central (simulada — nunca acessa a rede), valor manual."""

import datetime
import json
import urllib.error
from decimal import Decimal
from typing import Any

import pytest
from django.core.management import CommandError, call_command
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.investments import market
from apps.investments.forms import parse_percent
from apps.investments.market import MarketDataError, fetch_series, parse_sgs, update_rates
from apps.investments.models import MarketRate, RateOverride, Series
from apps.investments.selectors import effective_rates
from apps.investments.services import set_rate_override

D = datetime.date
SAMPLE = {
    4389: [{"data": "09/10/2026", "valor": "14.90"}, {"data": "13/10/2026", "valor": "14.90"}],
    432: [{"data": "13/10/2026", "valor": "15.00"}],
    13522: [{"data": "01/09/2026", "valor": "4.85"}],
}


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(timezone, "localdate", lambda *a, **k: D(2026, 10, 14))


def fake_fetch(url: str) -> bytes:
    code = int(url.split("bcdata.sgs.")[1].split("/")[0])
    return json.dumps(SAMPLE[code]).encode()


def failing_fetch(url: str) -> bytes:
    raise urllib.error.URLError("sem rede")


class TestParsing:
    def test_parse_sgs(self) -> None:
        assert parse_sgs(b'[{"data": "13/10/2026", "valor": "14.90"}]') == [
            (D(2026, 10, 13), Decimal("14.90"))
        ]

    @pytest.mark.parametrize(
        ("raw", "message"),
        [
            (b"<html>erro</html>", "inesperada"),
            (b"[]", "não retornou"),
            (b'[{"data": "13/10/2026", "valor": "1490"}]', "fora do esperado"),
        ],
    )
    def test_rejects_bad_payloads(self, raw: bytes, message: str) -> None:
        with pytest.raises(MarketDataError, match=message):
            parse_sgs(raw)

    def test_network_errors_become_friendly(self) -> None:
        with pytest.raises(MarketDataError, match="Não foi possível falar com o Banco Central"):
            fetch_series(Series.CDI, today=D(2026, 10, 14), fetch=failing_fetch)

    def test_only_bcb_https(self) -> None:
        with pytest.raises(MarketDataError, match="não permitido"):
            market._http_get("file:///etc/passwd")

    def test_parse_percent(self) -> None:
        assert parse_percent("14,90") == Decimal("14.90")
        assert parse_percent(" 14.9% ") == Decimal("14.9")
        assert parse_percent("") is None


@pytest.mark.django_db
class TestStorage:
    def test_update_is_idempotent_and_keeps_history(self) -> None:
        latest = update_rates(fetch=fake_fetch)
        assert latest[Series.CDI].date == D(2026, 10, 13)
        update_rates(fetch=fake_fetch)
        assert MarketRate.objects.filter(series=Series.CDI).count() == 2

    def test_future_dates_are_not_stored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(timezone, "localdate", lambda *a, **k: D(2026, 10, 12))
        latest = update_rates(fetch=fake_fetch)
        assert latest[Series.CDI].date == D(2026, 10, 9)  # 13/10 ainda não chegou
        assert not MarketRate.objects.filter(date__gt=D(2026, 10, 12)).exists()

    def test_all_or_nothing(self) -> None:
        def half(url: str) -> bytes:
            if "13522" in url:
                raise urllib.error.URLError("caiu")
            return fake_fetch(url)

        with pytest.raises(MarketDataError):
            update_rates(fetch=half)
        assert not MarketRate.objects.exists()

    def test_manual_value_wins_and_is_per_user(self, user: Any, other_user: Any) -> None:
        update_rates(fetch=fake_fetch)
        set_rate_override(user, Series.CDI, Decimal("13.50"))
        mine, theirs = effective_rates(user)[Series.CDI], effective_rates(other_user)[Series.CDI]
        assert (mine.annual_percent, mine.source) == (Decimal("13.50"), "informada por você")
        assert mine.annual == Decimal("0.135")
        assert theirs.annual_percent == Decimal("14.90")
        assert theirs.source == "Banco Central, 13/10/2026"
        set_rate_override(user, Series.CDI, None)
        assert effective_rates(user)[Series.CDI].manual is None

    def test_staleness(self, user: Any) -> None:
        assert effective_rates(user)[Series.SELIC].is_stale(D(2026, 10, 14))  # sem dado
        update_rates(fetch=fake_fetch)
        rate = effective_rates(user)[Series.SELIC]
        assert not rate.is_stale(D(2026, 10, 14))
        assert rate.is_stale(D(2026, 11, 1))


@pytest.mark.django_db
class TestViews:
    def test_page_and_update_button(
        self, client: Client, user: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(market, "_http_get", fake_fetch)
        client.force_login(user)
        follow = client.post(reverse("investments:rates_update"), follow=True).content.decode()
        assert "Taxas atualizadas: CDI 14,90%" in follow
        assert "Banco Central, 13/10/2026" in follow

    def test_update_failure_is_a_message(
        self, client: Client, user: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(market, "_http_get", failing_fetch)
        client.force_login(user)
        follow = client.post(reverse("investments:rates_update"), follow=True)
        assert follow.status_code == 200
        assert "Tente mais tarde" in follow.content.decode()

    def test_override_form(self, client: Client, user: Any) -> None:
        client.force_login(user)
        url = reverse("investments:rate_override")
        client.post(url, {"series": "cdi", "value": "14,25"})
        assert RateOverride.objects.get(owner=user).annual_percent == Decimal("14.25")
        bad = client.post(url, {"series": "cdi", "value": "abc"}, follow=True).content.decode()
        assert "Use um número" in bad
        absurd = client.post(url, {"series": "cdi", "value": "500"}, follow=True).content.decode()
        assert "entre -50% e 100%" in absurd
        client.post(url, {"series": "cdi", "value": ""})
        assert not RateOverride.objects.exists()

    def test_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(market, "_http_get", failing_fetch)
        with pytest.raises(CommandError, match="Banco Central"):
            call_command("atualizar_taxas")


def test_queries_a_date_window_ending_today() -> None:
    urls: list[str] = []

    def capture(url: str) -> bytes:
        urls.append(url)
        return json.dumps(SAMPLE[4389]).encode()

    fetch_series(Series.CDI, today=D(2026, 10, 14), fetch=capture)
    assert urls == [
        "https://api.bcb.gov.br/dados/serie/bcdata.sgs.4389/dados"
        "?formato=json&dataInicial=15/08/2026&dataFinal=14/10/2026"
    ]
