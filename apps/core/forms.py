from typing import Any

from django import forms
from django.core.exceptions import ValidationError

from apps.core.money import NBSP, InvalidMoneyError, format_brl, parse_brl


class MoneyField(forms.Field):
    """Campo de valor em reais; `cleaned_data` é um `int` de centavos (> 0 por padrão)."""

    def __init__(self, *, allow_zero: bool = False, **kwargs: Any) -> None:
        self.allow_zero = allow_zero
        super().__init__(**kwargs)

    def widget_attrs(self, widget: forms.Widget) -> dict[str, Any]:
        attrs = super().widget_attrs(widget)
        attrs.update(
            {
                "inputmode": "decimal",
                "maxlength": "32",
                "autocomplete": "off",
                "placeholder": "0,00",
            }
        )
        return attrs

    def prepare_value(self, value: Any) -> Any:
        if isinstance(value, int):
            return format_brl(value).removeprefix(f"R${NBSP}")
        return value

    def to_python(self, value: Any) -> int | None:
        text = str(value).strip() if value is not None else ""
        if not text:
            return None
        try:
            cents = parse_brl(text)
        except InvalidMoneyError as exc:
            raise ValidationError(str(exc), code="invalid") from exc
        if cents == 0 and not self.allow_zero:
            raise ValidationError("O valor deve ser maior que zero.", code="min_value")
        return cents
