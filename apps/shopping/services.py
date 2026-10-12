import datetime
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.users.models import User

from .domain.prices import store_from_url
from .models import PriceObservation, PriceSource, WishItem


def save_item(item: WishItem, *, name: str, target_cents: int | None, notes: str = "") -> WishItem:
    item.name = name.strip()
    item.target_cents = target_cents
    item.notes = notes.strip()
    item.full_clean()
    item.save()
    return item


def create_item(user: User, *, name: str, target_cents: int | None, notes: str = "") -> WishItem:
    return save_item(WishItem(owner=user), name=name, target_cents=target_cents, notes=notes)


def set_item_done(item: WishItem, *, done: bool) -> WishItem:
    item.is_done = done
    item.save(update_fields=["is_done", "updated_at"])
    return item


def delete_item(item: WishItem) -> None:
    item.delete()


@dataclass(frozen=True)
class ObservationData:
    price_cents: int
    url: str = ""
    store: str = ""
    shipping_cents: int = 0
    installments: int | None = None
    observed_on: datetime.date | None = None
    source: str = PriceSource.MANUAL


def add_observation(item: WishItem, data: ObservationData) -> PriceObservation:
    """Registra um preço. Sem nome de loja, ele vem do domínio do link."""
    store = data.store.strip() or (store_from_url(data.url) if data.url else "")
    if not store:
        raise ValidationError({"store": "Informe a loja ou cole o link do produto."})
    observed_on = data.observed_on or timezone.localdate()
    if observed_on > timezone.localdate():
        raise ValidationError({"observed_on": "A data não pode ser futura."})
    obs = PriceObservation(
        owner_id=item.owner_id,
        item=item,
        store=store[:80],
        url=data.url.strip(),
        price_cents=data.price_cents,
        shipping_cents=data.shipping_cents,
        installments=data.installments,
        observed_on=observed_on,
        source=data.source,
    )
    obs.full_clean()
    obs.save()
    return obs


def delete_observation(obs: PriceObservation) -> None:
    obs.delete()
