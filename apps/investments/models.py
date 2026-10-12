from django.db import models

from apps.core.models import OwnedModel


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
