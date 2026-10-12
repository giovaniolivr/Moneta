from decimal import Decimal, InvalidOperation
from typing import Any

from django import forms

from apps.core.forms import MoneyField, has_data

from .domain.products import PRESETS, Indexer, Taxation
from .domain.simulation import MAX_MONTHS
from .models import InvestmentProduct, Series
from .services import ProductData


def parse_percent(text: str) -> Decimal | None:
    """ "14,90" / "14.90" / "14,9%" → Decimal("14.90"); vazio → None."""
    text = text.strip().replace("%", "").replace(" ", "")
    if not text:
        return None
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        raise forms.ValidationError("Use um número, ex.: 14,90.") from None


class RateOverrideForm(forms.Form):
    series = forms.ChoiceField(choices=Series.choices)
    value = forms.CharField(label="% a.a.", required=False, max_length=12)

    def clean_value(self) -> Decimal | None:
        return parse_percent(self.cleaned_data.get("value") or "")


class ProductForm(forms.Form):
    name = forms.CharField(label="Nome", max_length=60)
    indexer = forms.ChoiceField(
        label="Rende por",
        choices=[
            (Indexer.CDI, "% do CDI"),
            (Indexer.SELIC, "% da Selic"),
            (Indexer.PREFIXED, "Prefixado (% a.a.)"),
            (Indexer.POUPANCA, "Poupança (regra oficial)"),
        ],
    )
    rate = forms.CharField(
        label="Taxa (%)",
        required=False,
        max_length=12,
        help_text="Ex.: 120 para 120% do CDI, ou 12,5 para 12,5% a.a. prefixado.",
    )
    taxation = forms.ChoiceField(
        label="Tributação",
        choices=[
            (Taxation.TAXED, "IR regressivo + IOF (CDB, RDB, Caixinha, Tesouro)"),
            (Taxation.IR_EXEMPT, "Isento de IR (LCI/LCA)"),
            (Taxation.EXEMPT, "Isento (poupança)"),
        ],
    )
    daily_liquidity = forms.BooleanField(label="Resgate a qualquer dia", required=False)

    def __init__(self, *args: Any, product: InvestmentProduct | None = None, **kwargs: Any) -> None:
        if product is not None and "initial" not in kwargs and not has_data(args, kwargs):
            kwargs["initial"] = {
                "name": product.name,
                "indexer": product.indexer,
                "rate": f"{product.rate_percent.normalize():f}".replace(".", ",")
                if product.rate_percent is not None
                else "",
                "taxation": product.taxation,
                "daily_liquidity": product.daily_liquidity,
            }
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name != "daily_liquidity":
                field.widget.attrs.setdefault("class", "input")
        self.fields["rate"].widget.attrs["inputmode"] = "decimal"

    def clean_rate(self) -> Decimal | None:
        return parse_percent(self.cleaned_data.get("rate") or "")

    def to_data(self) -> ProductData:
        d = self.cleaned_data
        return ProductData(d["name"], d["indexer"], d["rate"], d["taxation"], d["daily_liquidity"])

    @staticmethod
    def preset_initial(key: str) -> dict[str, Any] | None:
        preset = PRESETS.get(key)
        if preset is None:
            return None
        rate = preset["rate_percent"]
        return {
            **preset,
            "rate": f"{rate}".replace(".", ",") if rate is not None else "",
        }


class SimulationForm(forms.Form):
    """Parâmetros em português na URL (como na lista de movimentações): dá para guardar o link."""

    inicial = MoneyField(label="Valor inicial", required=False, allow_zero=True)
    aporte = MoneyField(label="Aporte mensal", required=False, allow_zero=True)
    meses = forms.IntegerField(label="Prazo (meses)", min_value=1, max_value=MAX_MONTHS)
    produto = forms.MultipleChoiceField(
        label="Comparar", widget=forms.CheckboxSelectMultiple, required=False
    )

    def __init__(self, *args: Any, choices: list[tuple[str, str]], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["produto"].choices = choices  # type: ignore[attr-defined]
        for name in ("inicial", "aporte", "meses"):
            self.fields[name].widget.attrs["class"] = "input"
        self.fields["meses"].widget.attrs["inputmode"] = "numeric"

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        if not (data.get("inicial") or data.get("aporte")) and not self.errors:
            raise forms.ValidationError("Informe um valor inicial, um aporte mensal ou os dois.")
        return data
