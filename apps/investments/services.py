from decimal import Decimal

from apps.users.models import User

from .models import RateOverride, Series


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
