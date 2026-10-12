"""Dias úteis do mercado brasileiro (feriados nacionais), base da rentabilidade diária.

Feriados: fixos nacionais + móveis da Páscoa (Carnaval segunda e terça, Sexta-feira Santa,
Corpus Christi), como no calendário usado pelo CDI. 20/11 é nacional desde 2024.
"""

import datetime
from functools import cache


def easter(year: int) -> datetime.date:
    """Domingo de Páscoa (algoritmo gregoriano anônimo / Meeus-Jones-Butcher)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month, day = divmod(h + ell - 7 * m + 114, 31)
    return datetime.date(year, month, day + 1)


@cache
def holidays(year: int) -> frozenset[datetime.date]:
    e = easter(year)
    fixed = [(1, 1), (4, 21), (5, 1), (9, 7), (10, 12), (11, 2), (11, 15), (12, 25)]
    if year >= 2024:
        fixed.append((11, 20))
    days = {datetime.date(year, m, d) for m, d in fixed}
    for offset in (-48, -47, -2, 60):  # Carnaval (seg, ter), Sexta-feira Santa, Corpus Christi
        days.add(e + datetime.timedelta(days=offset))
    return frozenset(days)


def is_business_day(day: datetime.date) -> bool:
    return day.weekday() < 5 and day not in holidays(day.year)


def business_days(start: datetime.date, end: datetime.date) -> int:
    """Dias úteis em [start, end): quantas vezes o dinheiro rende entre aplicar e resgatar."""
    if end <= start:
        return 0
    count = 0
    day = start
    while day < end:
        if is_business_day(day):
            count += 1
        day += datetime.timedelta(days=1)
    return count
