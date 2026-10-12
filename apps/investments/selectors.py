import datetime
from dataclasses import dataclass
from decimal import Decimal

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser

from .models import MarketRate, RateOverride, Series

UserLike = AbstractBaseUser | AnonymousUser


@dataclass(frozen=True)
class EffectiveRate:
    series: Series
    published: MarketRate | None  # último valor do Banco Central
    manual: Decimal | None  # valor informado pelo usuário (tem prioridade)

    @property
    def annual_percent(self) -> Decimal | None:
        if self.manual is not None:
            return self.manual
        return self.published.annual_percent if self.published else None

    @property
    def annual(self) -> Decimal | None:
        """Como fração (0.149) para o núcleo de cálculo."""
        value = self.annual_percent
        return value / 100 if value is not None else None

    @property
    def source(self) -> str:
        if self.manual is not None:
            return "informada por você"
        if self.published is not None:
            return f"Banco Central, {self.published.date:%d/%m/%Y}"
        return "sem dado"

    def is_stale(self, today: datetime.date, days: int = 7) -> bool:
        return self.manual is None and (
            self.published is None or (today - self.published.date).days > days
        )


def effective_rates(user: UserLike) -> dict[Series, EffectiveRate]:
    manual = dict(RateOverride.objects.for_user(user).values_list("series", "annual_percent"))
    result = {}
    for series in Series:
        published = MarketRate.objects.filter(series=series).order_by("-date").first()
        result[series] = EffectiveRate(series, published, manual.get(series))
    return result
