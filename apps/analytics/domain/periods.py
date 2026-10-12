"""Períodos e comparações justas entre períodos (Python puro, sem Django).

Regra central: um período em andamento só é comparado com o MESMO número de dias do período
anterior (pro-rata). Ex.: no dia 10, "este mês" (1-10) é comparado com 1-10 do mês passado,
nunca com o mês passado inteiro.
"""

import calendar
import datetime
from dataclasses import dataclass


def add_months(day: datetime.date, months: int) -> datetime.date:
    """Soma meses mantendo o dia quando possível (31/03 - 1 mês = 28 ou 29/02)."""
    index = day.year * 12 + day.month - 1 + months
    year, month = divmod(index, 12)
    last = calendar.monthrange(year, month + 1)[1]
    return datetime.date(year, month + 1, min(day.day, last))


def month_start(day: datetime.date) -> datetime.date:
    return day.replace(day=1)


def month_end(day: datetime.date) -> datetime.date:
    return day.replace(day=calendar.monthrange(day.year, day.month)[1])


def months_between(first: datetime.date, last: datetime.date) -> list[datetime.date]:
    """Primeiro dia de cada mês de `first` até `last`, inclusive."""
    months: list[datetime.date] = []
    current = month_start(first)
    while current <= last:
        months.append(current)
        current = add_months(current, 1)
    return months


def first_full_month(first_day: datetime.date) -> datetime.date:
    """Primeiro mês inteiramente coberto a partir de `first_day` (um mês que começou no meio
    não serve de base histórica: as despesas dele estariam subestimadas)."""
    return first_day if first_day.day == 1 else add_months(month_start(first_day), 1)


def _is_calendar_span(start: datetime.date, end: datetime.date) -> bool:
    return start.day == 1 and end == month_end(end)


@dataclass(frozen=True)
class DateRange:
    start: datetime.date
    end: datetime.date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


@dataclass(frozen=True)
class Comparison:
    """Recorte efetivo do período atual e o recorte equivalente do período anterior."""

    current: DateRange
    previous: DateRange
    partial: bool  # o período atual ainda está em andamento (comparação pro-rata)


def comparable_ranges(
    start: datetime.date, end: datetime.date, today: datetime.date
) -> Comparison | None:
    """Recorte atual (até hoje) e o recorte anterior com o mesmo tamanho.

    Períodos de meses inteiros (mês, ano) são deslocados em meses de calendário; os demais
    (ex.: últimos 30 dias) são deslocados pelo próprio comprimento. Retorna None quando o
    período ainda não começou.
    """
    effective_end = min(end, today)
    if effective_end < start:
        return None
    current = DateRange(start, effective_end)
    if _is_calendar_span(start, end):
        span_months = len(months_between(start, end))
        prev_start = add_months(start, -span_months)
        prev_period_end = month_end(add_months(end, -span_months))
        # Período fechado: o anterior inteiro. Em andamento: o mesmo número de dias (pro-rata).
        prorated_end = prev_start + (effective_end - start)
        prev_end = prev_period_end if effective_end == end else min(prorated_end, prev_period_end)
    else:
        prev_end = start - datetime.timedelta(days=1)
        prev_start = prev_end - (effective_end - start)
    return Comparison(current, DateRange(prev_start, prev_end), partial=effective_end < end)
