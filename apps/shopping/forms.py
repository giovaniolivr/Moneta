from typing import Any

from django import forms
from django.utils import timezone

from apps.core.forms import MoneyField, has_data
from apps.core.money import NBSP, format_brl

from .models import WishItem


def _money_text(cents: int | None) -> str:
    return format_brl(cents).removeprefix(f"R${NBSP}") if cents else ""


class WishItemForm(forms.Form):
    name = forms.CharField(label="O que você quer comprar", max_length=120)
    target = MoneyField(
        label="Preço-alvo",
        required=False,
        help_text="Opcional: avisamos quando o melhor preço chegar a este valor.",
    )
    notes = forms.CharField(
        label="Observações", required=False, widget=forms.Textarea(attrs={"rows": 2})
    )

    def __init__(self, *args: Any, item: WishItem | None = None, **kwargs: Any) -> None:
        if item is not None and "initial" not in kwargs and not has_data(args, kwargs):
            kwargs["initial"] = {
                "name": item.name,
                "target": _money_text(item.target_cents),
                "notes": item.notes,
            }
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "input")


class ObservationForm(forms.Form):
    url = forms.URLField(
        label="Link do produto",
        required=False,
        max_length=500,
        assume_scheme="https",
        help_text="Cole o link: a loja é reconhecida pelo endereço.",
    )
    store = forms.CharField(label="Loja", required=False, max_length=80)
    price = MoneyField(label="Preço")
    shipping = MoneyField(label="Frete", required=False, allow_zero=True)
    installments = forms.IntegerField(
        label="Parcelas sem juros", required=False, min_value=2, max_value=48
    )
    observed_on = forms.DateField(
        label="Data", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["observed_on"].initial = timezone.localdate
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "input")
        self.fields["url"].widget.attrs.update({"inputmode": "url", "autocomplete": "off"})
        self.fields["price"].widget.attrs.update({"autofocus": True})
