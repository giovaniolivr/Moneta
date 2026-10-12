"""Projeção de saldo dia a dia e faturas previstas de cartão (Python puro, sem Django).

Parte dos saldos realizados de hoje e aplica:
- as movimentações previstas (as vencidas e não confirmadas contam como se fossem hoje);
- o pagamento de cada fatura no vencimento: no fechamento, o valor devido no cartão vira um
  pagamento que sai da conta pagadora na data de vencimento (como pagar a fatura inteira).
  Faturas são calculadas, nunca gravadas: quando o usuário registra o pagamento de verdade, a
  transferência realizada entra no saldo e a próxima projeção já parte dela.
"""

import calendar
import datetime
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID

from apps.investments.domain.calendar import is_business_day


def day_in_month(year: int, month: int, day: int) -> datetime.date:
    """Dia `day` do mês, limitado ao último (31 em fevereiro → 28/29)."""
    return datetime.date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _next_month(day: datetime.date) -> tuple[int, int]:
    return (day.year + 1, 1) if day.month == 12 else (day.year, day.month + 1)


@dataclass(frozen=True)
class Billing:
    card_id: UUID
    payer_id: UUID
    closing_day: int
    due_day: int

    def closing_on_or_after(self, day: datetime.date) -> datetime.date:
        closing = day_in_month(day.year, day.month, self.closing_day)
        if closing >= day:
            return closing
        return day_in_month(*_next_month(day), self.closing_day)

    def last_closing_before(self, day: datetime.date) -> datetime.date:
        closing = day_in_month(day.year, day.month, self.closing_day)
        if closing < day:
            return closing
        year, month = (day.year - 1, 12) if day.month == 1 else (day.year, day.month - 1)
        return day_in_month(year, month, self.closing_day)

    def due_for(self, closing: datetime.date) -> datetime.date:
        """Vencimento da fatura que fecha em `closing` (no mesmo mês ou no seguinte)."""
        due = day_in_month(closing.year, closing.month, self.due_day)
        return due if due > closing else day_in_month(*_next_month(closing), self.due_day)


@dataclass(frozen=True)
class Event:
    day: datetime.date
    account_id: UUID
    delta_cents: int  # + entra, - sai (para cartão: - é compra, + é pagamento)


@dataclass(frozen=True)
class PendingPayment:
    """Fatura já fechada e ainda não paga (calculada a partir do histórico realizado)."""

    card_id: UUID
    closing: datetime.date
    due: datetime.date
    amount_cents: int


@dataclass
class Statement:
    card_id: UUID
    closing: datetime.date
    due: datetime.date
    amount_cents: int
    planned_cents: int  # parte que ainda é prevista (parcelas, assinaturas no cartão)
    closed: bool  # já fechada (só falta pagar)


@dataclass
class Projection:
    days: list[datetime.date]
    balances: dict[UUID, list[int]]  # saldo de cada conta ao fim de cada dia
    statements: list[Statement] = field(default_factory=list)
    yields: dict[UUID, int] = field(default_factory=dict)  # rendimento estimado no período

    def balance_on(self, account_id: UUID, day: datetime.date) -> int:
        index = min(max((day - self.days[0]).days, 0), len(self.days) - 1)
        return self.balances[account_id][index]

    def lowest(self, account_id: UUID) -> tuple[datetime.date, int]:
        values = self.balances[account_id]
        index = min(range(len(values)), key=values.__getitem__)
        return self.days[index], values[index]

    def first_negative(self, account_id: UUID) -> datetime.date | None:
        for day, value in zip(self.days, self.balances[account_id], strict=True):
            if value < 0:
                return day
        return None


def project(
    *,
    today: datetime.date,
    end: datetime.date,
    opening: dict[UUID, int],
    events: Iterable[Event],
    billings: Sequence[Billing] = (),
    pending: Sequence[PendingPayment] = (),
    daily_rates: Mapping[UUID, Decimal] | None = None,
) -> Projection:
    """`daily_rates`: rendimento líquido por dia útil de cada conta (fração), aplicado no fim do
    dia sobre o saldo positivo. Frações de centavo acumulam até virar centavo."""
    rates = daily_rates or {}
    carry: dict[UUID, Decimal] = defaultdict(Decimal)
    earned: dict[UUID, int] = defaultdict(int)
    by_day: dict[datetime.date, list[Event]] = defaultdict(list)
    for event in events:
        by_day[max(event.day, today)].append(event)

    payments: dict[datetime.date, list[tuple[Billing, int]]] = defaultdict(list)
    billing_of = {b.card_id: b for b in billings}
    statements: list[Statement] = []
    for p in pending:
        billing = billing_of.get(p.card_id)
        if billing is None or p.due < today or p.amount_cents <= 0:
            continue
        payments[p.due].append((billing, p.amount_cents))
        statements.append(Statement(p.card_id, p.closing, p.due, p.amount_cents, 0, closed=True))

    cycle_planned: dict[UUID, int] = defaultdict(int)
    balances = dict(opening)
    days: list[datetime.date] = []
    series: dict[UUID, list[int]] = {account: [] for account in balances}
    day = today
    while day <= end:
        for event in by_day.get(day, ()):
            balances[event.account_id] = balances.get(event.account_id, 0) + event.delta_cents
            # Todo evento é previsto (o realizado já está no saldo de abertura).
            if event.account_id in billing_of and event.delta_cents < 0:
                cycle_planned[event.account_id] += -event.delta_cents
        for billing, amount in payments.get(day, ()):
            balances[billing.card_id] += amount
            balances[billing.payer_id] = balances.get(billing.payer_id, 0) - amount
        for billing in billings:
            if billing.closing_on_or_after(day) != day:
                continue
            owed = max(0, -balances.get(billing.card_id, 0))
            due = billing.due_for(day)
            statements.append(
                Statement(
                    billing.card_id,
                    day,
                    due,
                    owed,
                    min(cycle_planned[billing.card_id], owed),
                    closed=False,
                )
            )
            cycle_planned[billing.card_id] = 0
            if owed and due <= end:
                payments[due].append((billing, owed))
        if rates and is_business_day(day):
            for account, rate in rates.items():
                if balances.get(account, 0) > 0:
                    carry[account] += balances[account] * rate
                    whole = int(carry[account])
                    if whole:
                        balances[account] += whole
                        earned[account] += whole
                        carry[account] -= whole
        days.append(day)
        for account in series:
            series[account].append(balances.get(account, 0))
        day += datetime.timedelta(days=1)
    statements.sort(key=lambda s: (s.card_id, s.closing))
    return Projection(days, series, statements, dict(earned))
