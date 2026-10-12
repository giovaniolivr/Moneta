from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import NormalizedNameModel, OwnedModel

from .domain.products import Indexer, ProductSpec, Taxation


class Series(models.TextChoices):
    CDI = "cdi", "CDI"
    SELIC = "selic", "Selic"
    IPCA = "ipca", "IPCA (12 meses)"


class MarketRate(models.Model):
    """Taxa de mercado publicada (dado público, compartilhado — não pertence a um usuário).

    `annual_percent` em % a.a. (14.90 = 14,90%); para o IPCA, a variação acumulada em 12 meses.
    """

    series = models.CharField("série", max_length=10, choices=Series.choices)
    date = models.DateField("data")
    annual_percent = models.DecimalField("% a.a.", max_digits=8, decimal_places=4)
    fetched_at = models.DateTimeField("obtida em", auto_now=True)

    class Meta:
        verbose_name = "taxa de mercado"
        verbose_name_plural = "taxas de mercado"
        ordering = ["series", "-date"]
        constraints = [
            models.UniqueConstraint(fields=["series", "date"], name="marketrate_unique_day")
        ]

    def __str__(self) -> str:
        return f"{self.get_series_display()} {self.annual_percent}% em {self.date:%d/%m/%Y}"


class RateOverride(OwnedModel):
    """Valor informado pelo usuário: vale nas simulações dele no lugar do publicado."""

    series = models.CharField("série", max_length=10, choices=Series.choices)
    annual_percent = models.DecimalField("% a.a.", max_digits=8, decimal_places=4)

    class Meta:
        verbose_name = "taxa informada"
        verbose_name_plural = "taxas informadas"
        constraints = [
            models.UniqueConstraint(fields=["owner", "series"], name="rateoverride_unique_series"),
            models.CheckConstraint(
                condition=models.Q(annual_percent__gt=-50, annual_percent__lt=100),
                name="rateoverride_plausible",
                # "%%": o Django formata esta mensagem com o operador %.
                violation_error_message="Informe uma taxa entre -50%% e 100%% ao ano.",
            ),
        ]


class InvestmentProduct(NormalizedNameModel, OwnedModel):
    """Como um dinheiro rende (ex.: Caixinha 100% do CDI). Configuração do usuário — as taxas de
    mercado ficam em `MarketRate`/`RateOverride`."""

    indexer = models.CharField(
        "indexador",
        max_length=10,
        choices=[
            (Indexer.CDI, "% do CDI"),
            (Indexer.SELIC, "% da Selic"),
            (Indexer.PREFIXED, "Prefixado (% a.a.)"),
            (Indexer.POUPANCA, "Poupança"),
        ],
    )
    rate_percent = models.DecimalField(
        "taxa (%)", max_digits=7, decimal_places=3, null=True, blank=True
    )
    taxation = models.CharField(
        "tributação",
        max_length=10,
        choices=[
            (Taxation.TAXED, "IR regressivo + IOF"),
            (Taxation.IR_EXEMPT, "Isento de IR (LCI/LCA)"),
            (Taxation.EXEMPT, "Isento (poupança)"),
        ],
        default=Taxation.TAXED,
    )
    daily_liquidity = models.BooleanField("liquidez diária", default=True)

    class Meta:
        verbose_name = "produto de investimento"
        verbose_name_plural = "produtos de investimento"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "normalized_name"],
                name="investmentproduct_unique_name",
                violation_error_message="Já existe um produto com este nome.",
            ),
            models.CheckConstraint(
                condition=models.Q(rate_percent__isnull=True)
                | models.Q(rate_percent__gt=0, rate_percent__lt=1000),
                name="investmentproduct_rate_range",
                violation_error_message="Informe uma taxa maior que zero.",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        if self.indexer != Indexer.POUPANCA and self.rate_percent is None:
            raise ValidationError({"rate_percent": "Informe a taxa que o banco paga."})
        if self.indexer == Indexer.POUPANCA:
            self.rate_percent = None
            self.taxation = Taxation.EXEMPT

    @property
    def spec(self) -> ProductSpec:
        return ProductSpec(Indexer(self.indexer), self.rate_percent, Taxation(self.taxation))

    @property
    def rate_label(self) -> str:
        match self.indexer:
            case Indexer.POUPANCA:
                return "Poupança"
            case Indexer.PREFIXED:
                return f"{self.rate_percent:.2f}% a.a.".replace(".", ",")
            case Indexer.SELIC:
                return f"{self.rate_percent:.0f}% da Selic"
            case _:
                return f"{self.rate_percent:.0f}% do CDI"
