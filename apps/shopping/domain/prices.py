"""Lojas e preços (Python puro): de onde veio o link e qual é o melhor preço agora."""

import datetime
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

# Domínio → nome de exibição. Lojas fora da lista aparecem pelo domínio.
KNOWN_STORES = {
    "kabum.com.br": "KaBuM!",
    "pichau.com.br": "Pichau",
    "terabyteshop.com.br": "Terabyte",
    "mercadolivre.com.br": "Mercado Livre",
    "mercadolibre.com": "Mercado Livre",
    "amazon.com.br": "Amazon",
    "magazineluiza.com.br": "Magazine Luiza",
    "magalu.com": "Magazine Luiza",
}
FRESH_DAYS = 30  # observações mais antigas não contam como "preço agora"
LOW_WINDOW_DAYS = 60


def store_from_url(url: str) -> str:
    """ "https://www.kabum.com.br/produto/123" → "KaBuM!"; desconhecida → "loja.com.br"."""
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    for domain, name in KNOWN_STORES.items():
        if host == domain or host.endswith("." + domain):
            return name
    return host


@dataclass(frozen=True)
class Observation:
    store: str
    day: datetime.date
    price_cents: int
    shipping_cents: int = 0
    installments: int | None = None

    @property
    def total_cents(self) -> int:
        return self.price_cents + self.shipping_cents


@dataclass(frozen=True)
class PriceSummary:
    best_now: Observation | None  # menor (preço + frete) entre o último de cada loja, recente
    lowest_ever: Observation | None
    stores_now: list[Observation]  # último preço recente de cada loja, do menor ao maior
    target_cents: int | None

    @property
    def below_target(self) -> bool:
        return bool(
            self.best_now and self.target_cents and self.best_now.total_cents <= self.target_cents
        )

    @property
    def is_lowest_ever(self) -> bool:
        """O melhor preço de agora é o menor já registrado (empate conta)."""
        return bool(
            self.best_now
            and self.lowest_ever
            and self.best_now.total_cents <= self.lowest_ever.total_cents
        )


def summarize(
    observations: Sequence[Observation], *, today: datetime.date, target_cents: int | None
) -> PriceSummary:
    if not observations:
        return PriceSummary(None, None, [], target_cents)
    cutoff = today - datetime.timedelta(days=FRESH_DAYS)
    latest: dict[str, Observation] = {}
    for obs in sorted(observations, key=lambda o: o.day):
        latest[obs.store] = obs
    fresh = sorted(
        (o for o in latest.values() if o.day >= cutoff), key=lambda o: (o.total_cents, o.store)
    )
    lowest = min(observations, key=lambda o: (o.total_cents, o.day))
    return PriceSummary(fresh[0] if fresh else None, lowest, fresh, target_cents)


def previous_low(
    observations: Sequence[Observation],
    *,
    best: Observation,
    today: datetime.date,
    days: int = LOW_WINDOW_DAYS,
) -> int | None:
    """Menor preço + frete registrado na janela ANTES do melhor preço atual (None sem base)."""
    start = today - datetime.timedelta(days=days)
    prior = [o.total_cents for o in observations if start <= o.day < best.day]
    return min(prior) if prior else None
