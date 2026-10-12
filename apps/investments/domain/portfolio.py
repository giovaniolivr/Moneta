"""Carteira de um produto: aportes datados, resgates PEPS e valor bruto/líquido (puro).

Cada aporte é um lote com sua própria data (o IR regressivo e o IOF dependem de quanto tempo
AQUELE dinheiro ficou aplicado). Resgates consomem os lotes mais antigos primeiro (PEPS), como
fazem os bancos em CDB/RDB. Valores internos em Decimal (centavos); saídas arredondadas.
"""

import datetime
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal

from .rates import Yield
from .taxes import TaxRule


def cents(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


@dataclass
class Lot:
    day: datetime.date
    principal: Decimal  # centavos ainda aplicados deste aporte


@dataclass(frozen=True)
class Valuation:
    gross_cents: int
    iof_cents: int
    ir_cents: int
    principal_cents: int

    @property
    def net_cents(self) -> int:
        return self.gross_cents - self.iof_cents - self.ir_cents

    @property
    def gain_cents(self) -> int:
        return self.gross_cents - self.principal_cents


class InsufficientFundsError(Exception):
    pass


@dataclass
class Portfolio:
    yield_: Yield
    taxes: TaxRule
    lots: list[Lot] = field(default_factory=list)

    def deposit(self, day: datetime.date, amount_cents: int) -> None:
        if amount_cents > 0:
            self.lots.append(Lot(day, Decimal(amount_cents)))

    def _lot_parts(self, lot: Lot, day: datetime.date) -> tuple[Decimal, Decimal, Decimal]:
        gross = lot.principal * self.yield_.factor(lot.day, day)
        iof, ir = self.taxes.taxes(gross - lot.principal, (day - lot.day).days)
        return gross, iof, ir

    def value(self, day: datetime.date) -> Valuation:
        gross = iof = ir = principal = Decimal(0)
        for lot in self.lots:
            g, i, r = self._lot_parts(lot, day)
            gross, iof, ir, principal = gross + g, iof + i, ir + r, principal + lot.principal
        return Valuation(cents(gross), cents(iof), cents(ir), cents(principal))

    def withdraw_net(self, day: datetime.date, amount_cents: int) -> Valuation:
        """Resgata o necessário para receber `amount_cents` líquidos (PEPS).

        Retorna o que saiu: bruto, IOF e IR pagos e o principal consumido.
        """
        needed = Decimal(amount_cents)
        gross_out = iof_out = ir_out = principal_out = Decimal(0)
        remaining: list[Lot] = []
        for index, lot in enumerate(self.lots):
            if needed <= 0:
                remaining.extend(self.lots[index:])
                break
            g, i, r = self._lot_parts(lot, day)
            net = g - i - r
            share = min(Decimal(1), needed / net) if net > 0 else Decimal(1)
            gross_out += g * share
            iof_out += i * share
            ir_out += r * share
            principal_out += lot.principal * share
            needed -= net * share
            if share < 1:
                remaining.append(Lot(lot.day, lot.principal * (1 - share)))
                remaining.extend(self.lots[index + 1 :])
                needed = Decimal(0)
                break
        if needed > Decimal("0.5"):
            raise InsufficientFundsError(f"faltam {cents(needed)} centavos líquidos")
        self.lots = remaining
        return Valuation(cents(gross_out), cents(iof_out), cents(ir_out), cents(principal_out))
