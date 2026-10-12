"""Datas de ocorrência de uma recorrência (Python puro, sem Django).

Cada ocorrência é calculada a partir da data inicial (n-ésima ocorrência = início + n * passo),
nunca a partir da anterior: uma recorrência no dia 31 cai em 30/04 e 28/02, mas volta ao dia 31
em maio — sem "escorregar" para o 28 para sempre.
"""

import datetime
from collections.abc import Iterator
from enum import StrEnum

from apps.analytics.domain.periods import add_months

WEEKDAY_NAMES = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]


class Frequency(StrEnum):
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    YEARLY = "yearly"


def nth(start: datetime.date, frequency: Frequency, interval: int, n: int) -> datetime.date:
    match frequency:
        case Frequency.WEEKLY:
            return start + datetime.timedelta(weeks=interval * n)
        case Frequency.MONTHLY:
            return add_months(start, interval * n)
        case Frequency.YEARLY:
            return add_months(start, 12 * interval * n)


def occurrences(
    start: datetime.date,
    frequency: Frequency,
    interval: int,
    *,
    through: datetime.date,
    after: datetime.date | None = None,
    end: datetime.date | None = None,
) -> Iterator[datetime.date]:
    """Ocorrências em (`after`, `through`], sem passar de `end`."""
    if interval < 1:
        raise ValueError("interval deve ser >= 1")
    limit = min(through, end) if end else through
    n = 0
    while (day := nth(start, frequency, interval, n)) <= limit:
        if after is None or day > after:
            yield day
        n += 1


def next_occurrence(
    start: datetime.date,
    frequency: Frequency,
    interval: int,
    *,
    on_or_after: datetime.date,
    end: datetime.date | None = None,
) -> datetime.date | None:
    horizon = on_or_after + datetime.timedelta(days=366 * interval + 31)
    after = on_or_after - datetime.timedelta(days=1)
    return next(
        occurrences(start, frequency, interval, through=horizon, after=after, end=end), None
    )


def describe(start: datetime.date, frequency: Frequency, interval: int) -> str:
    """ "Todo mês no dia 5", "A cada 2 semanas (sexta)", "Todo ano em 15/03"."""
    match frequency:
        case Frequency.WEEKLY:
            weekday = WEEKDAY_NAMES[start.weekday()]
            return (
                f"Toda semana ({weekday})"
                if interval == 1
                else (f"A cada {interval} semanas ({weekday})")
            )
        case Frequency.MONTHLY:
            day = f"no dia {start.day}" if start.day <= 28 else f"no dia {start.day} (ou no último)"
            return f"Todo mês {day}" if interval == 1 else f"A cada {interval} meses, {day}"
        case Frequency.YEARLY:
            when = f"{start:%d/%m}"
            return f"Todo ano em {when}" if interval == 1 else f"A cada {interval} anos, em {when}"


def monthly_equivalent(amount_cents: int, frequency: Frequency, interval: int) -> int:
    """Valor médio por mês (para somar compromissos de frequências diferentes)."""
    per_year = {Frequency.WEEKLY: 52, Frequency.MONTHLY: 12, Frequency.YEARLY: 1}[frequency]
    return round(amount_cents * per_year / interval / 12)
