import datetime
from dataclasses import dataclass

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.db.models import QuerySet

from .domain.prices import (
    LOW_WINDOW_DAYS,
    Observation,
    PriceSummary,
    previous_low,
    summarize,
)
from .models import PriceObservation, WishItem

UserLike = AbstractBaseUser | AnonymousUser


def list_items(user: UserLike) -> QuerySet[WishItem]:
    return WishItem.objects.for_user(user).prefetch_related("observations")


def list_observations(user: UserLike) -> QuerySet[PriceObservation]:
    return PriceObservation.objects.for_user(user).select_related("item")


def _domain(obs: PriceObservation) -> Observation:
    return Observation(
        obs.store, obs.observed_on, obs.price_cents, obs.shipping_cents, obs.installments
    )


def summary(item: WishItem, today: datetime.date) -> PriceSummary:
    return summarize(
        [_domain(o) for o in item.observations.all()], today=today, target_cents=item.target_cents
    )


@dataclass(frozen=True)
class ItemRow:
    item: WishItem
    summary: PriceSummary


def item_rows(user: UserLike, today: datetime.date) -> list[ItemRow]:
    return [ItemRow(item, summary(item, today)) for item in list_items(user)]


def best_price_series(item: WishItem) -> list[tuple[datetime.date, int]]:
    """Melhor preço disponível (preço + frete) em cada data com registro: o menor entre o
    último preço conhecido de cada loja até aquela data."""
    latest: dict[str, int] = {}
    series: list[tuple[datetime.date, int]] = []
    for obs in sorted(item.observations.all(), key=lambda o: (o.observed_on, o.created_at)):
        latest[obs.store] = obs.total_cents
        if series and series[-1][0] == obs.observed_on:
            series.pop()
        series.append((obs.observed_on, min(latest.values())))
    return series


@dataclass(frozen=True)
class PriceAlert:
    item: WishItem
    best: Observation
    kind: str  # "target" (chegou ao alvo) ou "low" (menor dos últimos N dias)
    previous_low_cents: int | None
    window_days: int


def price_alerts(user: UserLike, today: datetime.date) -> list[PriceAlert]:
    """Itens ativos com preço bom AGORA: abaixo do alvo ou o menor da janela recente."""
    alerts = []
    for item in list_items(user).filter(is_done=False):
        observations = [_domain(o) for o in item.observations.all()]
        s = summarize(observations, today=today, target_cents=item.target_cents)
        if s.best_now is None:
            continue
        low = previous_low(observations, best=s.best_now, today=today)
        if s.below_target:
            alerts.append(PriceAlert(item, s.best_now, "target", low, LOW_WINDOW_DAYS))
        elif low is not None and s.best_now.total_cents < low:
            alerts.append(PriceAlert(item, s.best_now, "low", low, LOW_WINDOW_DAYS))
    return alerts
