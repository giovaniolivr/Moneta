"""Produto de investimento → regras de cálculo (Python puro).

Separa a CONFIGURAÇÃO do produto (indexador, percentual, tributação) das TAXAS de mercado: o
mesmo "120% do CDI" rende diferente conforme o CDI do momento.
"""

import datetime
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from .rates import CdiPercent, Poupanca, Prefixed, Yield
from .taxes import EXEMPT, EXEMPT_IR, TAXED, TaxRule


class Indexer(StrEnum):
    CDI = "cdi"  # % do CDI
    SELIC = "selic"  # % da Selic (Tesouro Selic)
    PREFIXED = "prefixed"  # % a.a. fixo
    POUPANCA = "poupanca"  # regra oficial


class Taxation(StrEnum):
    TAXED = "taxed"  # IR regressivo + IOF
    IR_EXEMPT = "ir_exempt"  # LCI/LCA
    EXEMPT = "exempt"  # poupança

    @property
    def rule(self) -> TaxRule:
        return {Taxation.TAXED: TAXED, Taxation.IR_EXEMPT: EXEMPT_IR, Taxation.EXEMPT: EXEMPT}[self]


class MissingRateError(Exception):
    """A taxa de mercado de que o produto depende (CDI/Selic) ainda não está disponível."""


@dataclass(frozen=True)
class ProductSpec:
    indexer: Indexer
    rate_percent: Decimal | None  # % do CDI/Selic ou % a.a. prefixado; poupança: ignorado
    taxation: Taxation

    def yield_for(self, *, cdi: Decimal | None, selic: Decimal | None) -> Yield:
        """Regra de rendimento com as taxas de mercado (frações: 0.1365 = 13,65% a.a.)."""
        rate = self.rate_percent if self.rate_percent is not None else Decimal(100)
        match self.indexer:
            case Indexer.CDI:
                if cdi is None:
                    raise MissingRateError("CDI")
                return CdiPercent(cdi, rate)
            case Indexer.SELIC:
                if selic is None:
                    raise MissingRateError("Selic")
                return CdiPercent(selic, rate)  # mesma mecânica: % de uma taxa DI diária
            case Indexer.PREFIXED:
                return Prefixed(rate / 100)
            case Indexer.POUPANCA:
                if selic is None:
                    raise MissingRateError("Selic")
                return Poupanca(selic)

    def one_year(
        self, *, cdi: Decimal | None, selic: Decimal | None, start: datetime.date
    ) -> tuple[Decimal, Decimal]:
        """(rentabilidade bruta, líquida) em 1 ano a partir de `start`, como fração."""
        end = start.replace(year=start.year + 1)
        gross = self.yield_for(cdi=cdi, selic=selic).factor(start, end) - 1
        iof, ir = self.taxation.rule.taxes(gross, (end - start).days)
        return gross, gross - iof - ir


# Modelos para começar: preenchem o formulário, o usuário confere e salva. Percentuais que
# dependem do banco (Turbo, CDB, LCI/LCA) vêm em branco de propósito.
PRESETS: dict[str, dict[str, object]] = {
    "caixinha": {
        "name": "Caixinha",
        "indexer": Indexer.CDI,
        "rate_percent": Decimal(100),
        "taxation": Taxation.TAXED,
        "daily_liquidity": True,
    },
    "caixinha-turbo": {
        "name": "Caixinha Turbo",
        "indexer": Indexer.CDI,
        "rate_percent": None,
        "taxation": Taxation.TAXED,
        "daily_liquidity": True,
    },
    "cdb": {
        "name": "CDB",
        "indexer": Indexer.CDI,
        "rate_percent": None,
        "taxation": Taxation.TAXED,
        "daily_liquidity": False,
    },
    "lci-lca": {
        "name": "LCI/LCA",
        "indexer": Indexer.CDI,
        "rate_percent": None,
        "taxation": Taxation.IR_EXEMPT,
        "daily_liquidity": False,
    },
    "tesouro-selic": {
        "name": "Tesouro Selic",
        "indexer": Indexer.SELIC,
        "rate_percent": Decimal(100),
        "taxation": Taxation.TAXED,
        "daily_liquidity": True,
    },
    "poupanca": {
        "name": "Poupança",
        "indexer": Indexer.POUPANCA,
        "rate_percent": None,
        "taxation": Taxation.EXEMPT,
        "daily_liquidity": True,
    },
}
