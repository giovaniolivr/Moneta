from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.template import Context, Template
from hypothesis import given
from hypothesis import strategies as st

from apps.core.forms import MoneyField
from apps.core.money import (
    MAX_CENTS,
    NBSP,
    InvalidMoneyError,
    cents_to_decimal,
    decimal_to_cents,
    format_brl,
    parse_brl,
    round_cents,
)


@pytest.mark.parametrize(
    ("text", "cents"),
    [
        ("45", 4500),
        ("45,9", 4590),
        ("45,90", 4590),
        ("0,01", 1),
        ("0", 0),
        ("1.234", 123400),
        ("45.900", 4590000),  # ponto + 3 dígitos é sempre milhar
        ("1.234,56", 123456),
        ("1234,56", 123456),
        ("1.234.567,89", 123456789),
        ("R$ 1.234,56", 123456),
        ("r$45", 4500),
        (f"R${NBSP}10,00", 1000),
        ("  45,90  ", 4590),
        ("45.9", 4590),
        ("45.90", 4590),
        ("1 234,56", 123456),
    ],
)
def test_parse_valid(text: str, cents: int) -> None:
    assert parse_brl(text) == cents


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "R$",
        "abc",
        "-45",
        "+45",
        "45,999",
        "1.23,45",
        "1.2345",
        "12.34.56",
        "1.000.0",
        "45,",
        ",45",
        "45,9,0",
        "1e3",
        "1,234.56",  # formato americano não é aceito
        "999.999.999.999.999,00",
    ],
)
def test_parse_invalid(text: str) -> None:
    with pytest.raises(InvalidMoneyError):
        parse_brl(text)


@pytest.mark.parametrize(
    ("cents", "expected"),
    [
        (0, "R$ 0,00"),
        (1, "R$ 0,01"),
        (4590, "R$ 45,90"),
        (123456, "R$ 1.234,56"),
        (123456789, "R$ 1.234.567,89"),
        (-123456, "-R$ 1.234,56"),
    ],
)
def test_format(cents: int, expected: str) -> None:
    assert format_brl(cents) == expected.replace(" ", NBSP)


def test_format_signed() -> None:
    assert format_brl(1000, signed=True) == f"+R${NBSP}10,00"
    assert format_brl(-1000, signed=True) == f"-R${NBSP}10,00"
    assert format_brl(0, signed=True) == f"R${NBSP}0,00"


@given(st.integers(min_value=0, max_value=MAX_CENTS))
def test_format_parse_roundtrip(cents: int) -> None:
    assert parse_brl(format_brl(cents)) == cents


@given(st.integers(min_value=-MAX_CENTS, max_value=MAX_CENTS))
def test_decimal_roundtrip(cents: int) -> None:
    assert decimal_to_cents(cents_to_decimal(cents)) == cents


@pytest.mark.parametrize("value", [Decimal("12.345"), Decimal("NaN"), Decimal("Infinity")])
def test_decimal_to_cents_rejects_invalid(value: Decimal) -> None:
    with pytest.raises(InvalidMoneyError):
        decimal_to_cents(value)


def test_decimal_to_cents_accepts_trailing_zeros() -> None:
    assert decimal_to_cents(Decimal("12.30")) == 1230
    assert decimal_to_cents(Decimal("12.300")) == 1230


def test_round_cents_is_bankers_rounding() -> None:
    assert round_cents(Decimal("10.5")) == 10
    assert round_cents(Decimal("11.5")) == 12
    assert round_cents(Decimal(100) / 3) == 33


def test_template_filters() -> None:
    tpl = Template("{% load money %}{{ a|brl }}|{{ b|brl_signed }}|{{ c|brl }}")
    out = tpl.render(Context({"a": 123456, "b": 500, "c": None}))
    assert out == f"R${NBSP}1.234,56|+R${NBSP}5,00|"


class TestMoneyField:
    def test_clean_returns_cents(self) -> None:
        assert MoneyField().clean("1.234,56") == 123456

    def test_rejects_zero_by_default(self) -> None:
        with pytest.raises(ValidationError):
            MoneyField().clean("0")
        assert MoneyField(allow_zero=True).clean("0") == 0

    def test_rejects_invalid(self) -> None:
        with pytest.raises(ValidationError):
            MoneyField().clean("12,345")

    def test_optional_empty_is_none(self) -> None:
        assert MoneyField(required=False).clean("") is None

    def test_widget_and_initial_display(self) -> None:
        field = MoneyField()
        assert field.widget.attrs["inputmode"] == "decimal"
        assert field.prepare_value(123456) == "1.234,56"
