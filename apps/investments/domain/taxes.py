"""Tributação de renda fixa sobre o rendimento (Python puro).

- IOF regressivo nos primeiros 29 dias corridos (tabela oficial: 96% → 3% do rendimento).
- IR regressivo por aporte, pelos dias corridos aplicados: 22,5% até 180 dias, 20% até 360,
  17,5% até 720 e 15% acima. Incide sobre o rendimento já descontado o IOF.
LCI/LCA e poupança são isentas de IR (pessoa física).
"""

from dataclasses import dataclass
from decimal import Decimal

IOF_TABLE = [
    96, 93, 90, 86, 83, 80, 76, 73, 70, 66, 63, 60, 56, 53, 50,
    46, 43, 40, 36, 33, 30, 26, 23, 20, 16, 13, 10, 6, 3,
]  # fmt: skip


def iof_rate(days: int) -> Decimal:
    """Alíquota de IOF sobre o rendimento para um resgate após `days` dias corridos."""
    if days < 1:
        return Decimal(1)  # resgate no mesmo dia: não há rendimento a tributar
    if days >= 30:
        return Decimal(0)
    return Decimal(IOF_TABLE[days - 1]) / 100


def ir_rate(days: int) -> Decimal:
    if days <= 180:
        return Decimal("0.225")
    if days <= 360:
        return Decimal("0.20")
    if days <= 720:
        return Decimal("0.175")
    return Decimal("0.15")


@dataclass(frozen=True)
class TaxRule:
    income_tax: bool = True  # IR regressivo
    iof: bool = True

    def taxes(self, gain: Decimal, days: int) -> tuple[Decimal, Decimal]:
        """(IOF, IR) sobre um rendimento `gain` resgatado após `days` dias corridos."""
        if gain <= 0:
            return Decimal(0), Decimal(0)
        iof = gain * iof_rate(days) if self.iof else Decimal(0)
        ir = (gain - iof) * ir_rate(days) if self.income_tax else Decimal(0)
        return iof, ir


TAXED = TaxRule()
EXEMPT = TaxRule(income_tax=False, iof=False)  # poupança
EXEMPT_IR = TaxRule(income_tax=False, iof=True)  # LCI/LCA
