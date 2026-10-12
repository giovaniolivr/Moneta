"""Acompanhamento de orçamento mensal (Python puro, sem Django).

O ritmo esperado NÃO é uma reta (limite * dias decorridos / dias do mês): com o aluguel no dia 6,
o orçamento de Moradia pareceria estourado no dia 7. Em vez disso, usa a fração do gasto que o
próprio usuário costuma fazer até aquele dia do mês, nos meses fechados (`typical_fraction`). Sem
histórico suficiente, cai para a reta — e diz isso (`method`).
"""

import datetime
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from apps.analytics.domain.metrics import percent

Status = Literal["ok", "pace", "over"]
Method = Literal["history", "linear", "closed"]

MIN_HISTORY_MONTHS = 2
MIN_ELAPSED_DAYS = 7  # antes disso, projetar o mês é ruído
MIN_PROJECTED_EXCESS_CENTS = 2_000


def typical_fraction(until_day: Sequence[int], whole_month: Sequence[int]) -> Decimal | None:
    """Fração do gasto do mês que costuma acontecer até o dia D (meses fechados).

    `until_day[i]` e `whole_month[i]` são o gasto do mês i até o dia D e no mês inteiro.
    Retorna None se não há meses suficientes com gasto.
    """
    pairs = [(u, w) for u, w in zip(until_day, whole_month, strict=True) if w > 0]
    if len(pairs) < MIN_HISTORY_MONTHS:
        return None
    return Decimal(sum(u for u, _ in pairs)) / Decimal(sum(w for _, w in pairs))


@dataclass(frozen=True)
class BudgetProgress:
    limit_cents: int
    spent_cents: int
    day: int  # dia do mês até onde há dados
    days_in_month: int
    partial: bool  # mês em andamento
    fraction: Decimal | None  # fração típica até `day` (None → reta)

    @property
    def method(self) -> Method:
        if not self.partial:
            return "closed"
        return "history" if self.fraction is not None else "linear"

    @property
    def expected_share(self) -> Decimal:
        """Fração do limite que "deveria" estar gasta até hoje."""
        if not self.partial:
            return Decimal(1)
        if self.fraction is not None:
            return self.fraction
        return Decimal(self.day) / Decimal(self.days_in_month)

    @property
    def expected_cents(self) -> int:
        return round(self.limit_cents * self.expected_share)

    @property
    def projected_cents(self) -> int | None:
        """Gasto projetado no fim do mês no ritmo atual (None se cedo demais para dizer)."""
        if not self.partial:
            return self.spent_cents
        if self.day < MIN_ELAPSED_DAYS or self.expected_share <= 0:
            return None
        return round(self.spent_cents / self.expected_share)

    @property
    def remaining_cents(self) -> int:
        return self.limit_cents - self.spent_cents

    @property
    def daily_allowance_cents(self) -> int | None:
        """Quanto ainda dá para gastar por dia até o fim do mês (contando hoje)."""
        days_left = self.days_in_month - self.day + 1
        if not self.partial or self.remaining_cents <= 0 or days_left <= 0:
            return None
        return self.remaining_cents // days_left

    @property
    def used_pct(self) -> Decimal | None:
        return percent(self.spent_cents, self.limit_cents)

    @property
    def status(self) -> Status:
        if self.spent_cents > self.limit_cents:
            return "over"
        projected = self.projected_cents
        if (
            self.partial
            and projected is not None
            and projected - self.limit_cents >= MIN_PROJECTED_EXCESS_CENTS
        ):
            return "pace"
        return "ok"


def progress(
    *,
    limit_cents: int,
    spent_cents: int,
    month_start: datetime.date,
    month_end: datetime.date,
    data_until: datetime.date,
    fraction: Decimal | None,
) -> BudgetProgress:
    partial = data_until < month_end
    return BudgetProgress(
        limit_cents=limit_cents,
        spent_cents=spent_cents,
        day=(min(data_until, month_end) - month_start).days + 1,
        days_in_month=(month_end - month_start).days + 1,
        partial=partial,
        fraction=fraction if partial else None,
    )
