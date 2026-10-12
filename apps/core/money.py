"""Valores monetários em centavos inteiros (BRL).

Módulo puro (sem Django): usado por forms, templates, serviços, exportação e, no futuro, pela API.
Toda quantia é um `int` de centavos — nunca `float`.
"""

import re
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

# R$ 999.999.999.999,99 — muito acima de qualquer uso pessoal; barra entradas absurdas.
MAX_CENTS = 99_999_999_999_999

NBSP = chr(0xA0)  # espaço não separável

_CURRENCY_PREFIX = re.compile(r"^r\$\s*", re.IGNORECASE)
# Parte inteira sem separador ou com milhares agrupados por ponto ("1234", "1.234").
_INTEGER = r"(?:\d+|\d{1,3}(?:\.\d{3})+)"
_BR_COMMA = re.compile(rf"^{_INTEGER},\d{{1,2}}$")
_BR_INTEGER = re.compile(rf"^{_INTEGER}$")
# Ponto como separador decimal (teclados numéricos de celular): "45.9", "45.90".
_DOT_DECIMAL = re.compile(r"^\d+\.\d{1,2}$")


class InvalidMoneyError(ValueError):
    """Texto que não representa um valor monetário válido."""


def parse_brl(text: str) -> int:
    """Converte o texto digitado pelo usuário em centavos (sempre >= 0).

    Aceita: "45", "45,9", "45,90", "1.234,56", "1234,56", "R$ 1.234,56", "45.90".
    Regras de ambiguidade:
    - Com vírgula: a vírgula é decimal e os pontos são separadores de milhar.
    - Sem vírgula: ponto seguido de 3 dígitos é milhar ("1.234"); de 1 ou 2, é decimal ("45.90").
    Rejeita sinais, mais de 2 casas decimais e agrupamentos inválidos ("1.23,4").
    """
    s = _CURRENCY_PREFIX.sub("", text.strip().replace(NBSP, " ")).replace(" ", "")
    if not s:
        raise InvalidMoneyError("Informe um valor.")

    if _BR_COMMA.match(s):
        integer, fraction = s.replace(".", "").split(",")
    elif _BR_INTEGER.match(s):
        integer, fraction = s.replace(".", ""), ""
    elif _DOT_DECIMAL.match(s):
        integer, fraction = s.split(".")
    else:
        raise InvalidMoneyError(f"Valor inválido: {text!r}.")

    cents = int(integer) * 100 + int(fraction.ljust(2, "0"))
    if cents > MAX_CENTS:
        raise InvalidMoneyError("Valor acima do limite permitido.")
    return cents


def format_brl(cents: int, *, signed: bool = False) -> str:
    """Formata centavos no padrão brasileiro: 123456 → "R$ 1.234,56".

    Usa espaço não separável entre "R$" e o número, para não quebrar linha.
    `signed=True` mostra "+" em valores positivos (útil para resultados e receitas).
    """
    integer, fraction = divmod(abs(cents), 100)
    number = f"{integer:,}".replace(",", ".")
    sign = "-" if cents < 0 else ("+" if signed and cents > 0 else "")
    return f"{sign}R${NBSP}{number},{fraction:02d}"


def cents_to_decimal(cents: int) -> Decimal:
    return Decimal(cents).scaleb(-2)


def decimal_to_cents(value: Decimal) -> int:
    """Converte um Decimal com no máximo 2 casas em centavos, sem arredondar silenciosamente."""
    try:
        scaled = value.scaleb(2)
        cents = scaled.to_integral_value()
    except InvalidOperation as exc:
        raise InvalidMoneyError(f"Valor inválido: {value!r}.") from exc
    if not scaled.is_finite() or scaled != cents:
        raise InvalidMoneyError(f"Valor inválido ou com mais de 2 casas decimais: {value}.")
    return int(cents)


def round_cents(value: Decimal) -> int:
    """Arredonda um resultado de cálculo em centavos (média, rateio) — arredondamento bancário."""
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_EVEN))
