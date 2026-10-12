"""Andamento de uma meta (Python puro, sem Django).

O progresso é o saldo da conta da meta (decisão da Fase 2). Aporte = transferências que
entram menos as que saem da conta, por mês — o rendimento NÃO entra no aporte (senão os
rendimentos que o usuário já lança seriam contados de novo). O rendimento estimado da conta
(`monthly_rate`, líquido de IR) entra à parte, com juros compostos mensais.
"""

import datetime
import math
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from apps.analytics.domain.metrics import monthly_average, percent
from apps.analytics.domain.periods import add_months, month_start

Status = Literal["achieved", "on_track", "behind", "beyond_capacity", "no_pace", "no_deadline"]
MAX_FORECAST_MONTHS = 1200


def months_until(today: datetime.date, deadline: datetime.date) -> int:
    """Meses de aporte até o prazo, contando o mês atual (prazo neste mês → 1)."""
    return max(1, (deadline.year - today.year) * 12 + deadline.month - today.month + 1)


def needed_payment(remaining_cents: int, balance_cents: int, months: int, rate: Decimal) -> int:
    """Aporte mensal para chegar a `balance + remaining` em `months`, rendendo `rate` ao mês.

    Valor futuro: saldo·(1+r)^n + aporte·((1+r)^n - 1)/r = alvo. Sem rendimento: falta / n.
    """
    target = Decimal(balance_cents + remaining_cents)
    if rate <= 0:
        return max(0, math.ceil(remaining_cents / months))
    growth = (1 + rate) ** months
    payment = (target - Decimal(balance_cents) * growth) * rate / (growth - 1)
    return max(0, math.ceil(payment))


@dataclass(frozen=True)
class GoalProgress:
    target_cents: int
    balance_cents: int
    today: datetime.date
    deadline: datetime.date | None
    pace_cents: int | None  # aporte médio mensal recente (None: sem meses suficientes)
    capacity_cents: int | None  # resultado médio mensal (receitas - despesas), meses fechados
    history_months: int
    monthly_rate: Decimal = Decimal(0)  # rendimento estimado líquido ao mês (fração)

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
        return needed_payment(
            self.remaining_cents, self.balance_cents, self.months_left or 1, self.monthly_rate
        )

    @property
    def forecast_month(self) -> datetime.date | None:
        """Mês em que alcança no ritmo atual (aportes + rendimento); None se não chega."""
        if self.remaining_cents == 0:
            return month_start(self.today)
        pace = Decimal(self.pace_cents or 0)
        if pace <= 0 and self.monthly_rate <= 0:
            return None
        balance = Decimal(self.balance_cents)
        for month in range(1, MAX_FORECAST_MONTHS + 1):
            balance = balance * (1 + self.monthly_rate) + pace
            if balance >= self.target_cents:
                return add_months(month_start(self.today), month - 1)
            if balance <= 0:
                return None
        return None

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
    monthly_contributions: Sequence[int],
    current_balance_cents: int,
    monthly_results: Sequence[int],
    today: datetime.date,
    deadline: datetime.date | None,
    monthly_rate: Decimal = Decimal(0),
) -> GoalProgress:
    """`monthly_contributions`: aporte líquido de cada mês fechado recente.
    `monthly_results`: resultado (receitas - despesas) de cada mês fechado."""
    return GoalProgress(
        target_cents=target_cents,
        balance_cents=current_balance_cents,
        today=today,
        deadline=deadline,
        pace_cents=monthly_average(list(monthly_contributions)),
        capacity_cents=monthly_average(list(monthly_results)),
        history_months=len(monthly_contributions),
        monthly_rate=monthly_rate,
    )
