from decimal import Decimal, InvalidOperation

from django import forms

from .models import Series


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
