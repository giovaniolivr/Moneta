"""Andamento de uma meta (Python puro, sem Django).

O progresso é o saldo da conta da meta (decisão da Fase 2). Rendimento ainda não é estimado
(Fase 3): projeções usam só os aportes.
"""

import datetime
import itertools
import math
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from apps.analytics.domain.metrics import monthly_average, percent
from apps.analytics.domain.periods import add_months, month_start

Status = Literal["achieved", "on_track", "behind", "beyond_capacity", "no_pace", "no_deadline"]


def months_until(today: datetime.date, deadline: datetime.date) -> int:
    """Meses de aporte até o prazo, contando o mês atual (prazo neste mês → 1)."""
    return max(1, (deadline.year - today.year) * 12 + deadline.month - today.month + 1)


def monthly_deltas(month_end_balances: Sequence[int]) -> list[int]:
    """Variação do saldo mês a mês (saldos no fim de meses consecutivos)."""
    return [b - a for a, b in itertools.pairwise(month_end_balances)]


@dataclass(frozen=True)
class GoalProgress:
    target_cents: int
    balance_cents: int
    today: datetime.date
    deadline: datetime.date | None
    pace_cents: int | None  # aporte médio mensal recente na conta (None: sem meses suficientes)
    capacity_cents: int | None  # resultado médio mensal (receitas - despesas), meses fechados
    history_months: int

    @property
    def remaining_cents(self) -> int:
        return max(0, self.target_cents - self.balance_cents)

    @property
    def pct(self) -> Decimal:
        value = percent(max(self.balance_cents, 0), self.target_cents) or Decimal(0)
        return min(value, Decimal(100))

    @property
    def months_left(self) -> int | None:
        return months_until(self.today, self.deadline) if self.deadline else None

    @property
    def needed_monthly_cents(self) -> int | None:
        if self.deadline is None or self.remaining_cents == 0:
            return None
        return math.ceil(self.remaining_cents / (self.months_left or 1))

    @property
    def forecast_month(self) -> datetime.date | None:
        """Mês em que alcança no ritmo atual de aportes (None se não há ritmo positivo)."""
        if self.remaining_cents == 0:
            return month_start(self.today)
        if not self.pace_cents or self.pace_cents <= 0:
            return None
        months = math.ceil(self.remaining_cents / self.pace_cents)
        return add_months(month_start(self.today), months - 1)

    @property
    def overdue(self) -> bool:
        return bool(self.deadline and self.deadline < self.today and self.remaining_cents)

    @property
    def status(self) -> Status:
        if self.remaining_cents == 0:
            return "achieved"
        needed = self.needed_monthly_cents
        if needed is None:
            return "no_deadline"
        if self.capacity_cents is not None and needed > max(self.capacity_cents, 0):
            return "beyond_capacity"
        if self.pace_cents is None:
            return "no_pace"
        return "on_track" if self.pace_cents >= needed else "behind"


def goal_progress(
    *,
    target_cents: int,
    month_end_balances: Sequence[int],
    current_balance_cents: int,
    monthly_results: Sequence[int],
    today: datetime.date,
    deadline: datetime.date | None,
) -> GoalProgress:
    """`month_end_balances`: saldo da conta no fim dos últimos meses fechados (+ o anterior a
    eles, para calcular a variação). `monthly_results`: resultado de cada mês fechado."""
    deltas = monthly_deltas(month_end_balances)
    return GoalProgress(
        target_cents=target_cents,
        balance_cents=current_balance_cents,
        today=today,
        deadline=deadline,
        pace_cents=monthly_average(deltas),
        capacity_cents=monthly_average(list(monthly_results)),
        history_months=len(deltas),
    )
