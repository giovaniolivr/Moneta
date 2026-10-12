"""Parcelamento (Python puro): divisão do valor e datas das parcelas."""

import datetime

from apps.analytics.domain.periods import add_months

MIN_INSTALLMENTS = 2
MAX_INSTALLMENTS = 48


def split(total_cents: int, count: int) -> list[int]:
    """Divide o total em `count` parcelas; a diferença de centavos fica na 1ª (como nos cartões).

    split(100_000, 3) → [33_334, 33_333, 33_333]. A soma é sempre exatamente o total.
    """
    if not MIN_INSTALLMENTS <= count <= MAX_INSTALLMENTS:
        raise ValueError(f"parcelas devem ficar entre {MIN_INSTALLMENTS} e {MAX_INSTALLMENTS}")
    if total_cents < count:
        raise ValueError("valor menor que um centavo por parcela")
    base, remainder = divmod(total_cents, count)
    return [base + remainder, *([base] * (count - 1))]


def due_dates(purchase: datetime.date, count: int) -> list[datetime.date]:
    """1ª parcela na data da compra; as demais no mesmo dia dos meses seguintes."""
    return [add_months(purchase, k) for k in range(count)]


def label(description: str, number: int, count: int) -> str:
    return f"{description} ({number}/{count})"
