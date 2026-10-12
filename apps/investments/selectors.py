import datetime
from dataclasses import dataclass
from decimal import Decimal

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.db.models import QuerySet

from .domain.products import MissingRateError
from .models import InvestmentProduct, MarketRate, RateOverride, Series

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

    def is_stale(self, today: datetime.date) -> bool:
        # O IPCA é mensal e sai com ~1 mês de defasagem; CDI e Selic são diários.
        days = 62 if self.series == Series.IPCA else 7
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


@dataclass(frozen=True)
class ProductRow:
    product: InvestmentProduct
    gross_year: Decimal | None  # rentabilidade em 1 ano, fração
    net_year: Decimal | None
    missing: str  # taxa de mercado que falta para estimar ("CDI", "Selic") ou ""
    accounts: list[str]


def list_products(user: UserLike) -> QuerySet[InvestmentProduct]:
    return InvestmentProduct.objects.for_user(user)


def product_rows(user: UserLike, today: datetime.date) -> list[ProductRow]:
    rates = effective_rates(user)
    cdi, selic = rates[Series.CDI].annual, rates[Series.SELIC].annual
    rows = []
    for product in list_products(user).prefetch_related("accounts"):
        gross: Decimal | None = None
        net: Decimal | None = None
        missing = ""
        try:
            gross, net = product.spec.one_year(cdi=cdi, selic=selic, start=today)
        except MissingRateError as exc:
            missing = str(exc)
        accounts = [a.name for a in product.accounts.all() if a.owner_id == product.owner_id]
        rows.append(ProductRow(product, gross, net, missing, accounts))
    return rows
