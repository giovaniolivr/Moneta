from dataclasses import dataclass
from decimal import Decimal

from apps.users.models import User

from .models import InvestmentProduct, RateOverride, Series


def set_rate_override(user: User, series: Series | str, annual_percent: Decimal | None) -> None:
    """Grava o valor manual da série; `None` volta a usar o do Banco Central."""
    if annual_percent is None:
        RateOverride.objects.for_user(user).filter(series=series).delete()
        return
    override = RateOverride.objects.for_user(user).filter(series=series).first() or RateOverride(
        owner=user, series=series
    )
    override.annual_percent = annual_percent
    override.full_clean()
    override.save()


@dataclass(frozen=True)
class ProductData:
    name: str
    indexer: str
    rate_percent: Decimal | None
    taxation: str
    daily_liquidity: bool


def _save_product(product: InvestmentProduct, data: ProductData) -> InvestmentProduct:
    product.name = data.name
    product.indexer = data.indexer
    product.rate_percent = data.rate_percent
    product.taxation = data.taxation
    product.daily_liquidity = data.daily_liquidity
    product.full_clean()
    product.save()
    return product


def create_product(user: User, data: ProductData) -> InvestmentProduct:
    return _save_product(InvestmentProduct(owner=user), data)


def update_product(product: InvestmentProduct, data: ProductData) -> InvestmentProduct:
    return _save_product(product, data)


def delete_product(product: InvestmentProduct) -> None:
    """As contas que usavam o produto ficam sem rendimento estimado (não são apagadas)."""
    product.delete()
