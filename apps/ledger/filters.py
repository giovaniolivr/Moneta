"""Filtro de movimentações compartilhado pela lista, pela dashboard e (no futuro) pela API.

Semântica: valores da MESMA dimensão combinam com OU (categoria A ou B); dimensões diferentes
combinam com E (categoria A e tag X). Uma conta casa com a origem ou com o destino da movimentação.
"""

import calendar
import datetime
import uuid
from dataclasses import dataclass
from urllib.parse import urlencode

PERIOD_CHOICES = [
    ("mes", "Este mês"),
    ("mes-anterior", "Mês anterior"),
    ("30d", "Últimos 30 dias"),
    ("ano", "Este ano"),
    ("tudo", "Todo o período"),
    ("personalizado", "Personalizado"),
]
DEFAULT_PERIOD = "mes"


def month_bounds(day: datetime.date) -> tuple[datetime.date, datetime.date]:
    last = calendar.monthrange(day.year, day.month)[1]
    return day.replace(day=1), day.replace(day=last)


def period_range(
    preset: str, today: datetime.date
) -> tuple[datetime.date | None, datetime.date | None]:
    """Intervalo (inclusivo) de um período predefinido. `tudo`/desconhecido → sem limites."""
    match preset:
        case "mes":
            return month_bounds(today)
        case "mes-anterior":
            return month_bounds(today.replace(day=1) - datetime.timedelta(days=1))
        case "30d":
            return today - datetime.timedelta(days=29), today
        case "ano":
            return today.replace(month=1, day=1), today.replace(month=12, day=31)
        case _:
            return None, None


@dataclass(frozen=True)
class TransactionFilter:
    date_from: datetime.date | None = None
    date_to: datetime.date | None = None
    kinds: tuple[str, ...] = ()
    category_ids: tuple[uuid.UUID, ...] = ()
    tag_ids: tuple[uuid.UUID, ...] = ()
    account_ids: tuple[uuid.UUID, ...] = ()
    payment_methods: tuple[str, ...] = ()
    search: str = ""
    # Só realizadas por padrão: análises, saldos e a lista nunca misturam projeções.
    status: str = "posted"  # "planned" ou "" (todas)

    def to_query(self) -> str:
        """Query string equivalente (para links de drill-down da dashboard)."""
        params: list[tuple[str, str]] = []
        if self.date_from or self.date_to:
            params.append(("periodo", "personalizado"))
            if self.date_from:
                params.append(("de", self.date_from.isoformat()))
            if self.date_to:
                params.append(("ate", self.date_to.isoformat()))
        else:
            params.append(("periodo", "tudo"))
        params += [("tipo", k) for k in self.kinds]
        params += [("categoria", str(i)) for i in self.category_ids]
        params += [("tag", str(i)) for i in self.tag_ids]
        params += [("conta", str(i)) for i in self.account_ids]
        params += [("pagamento", m) for m in self.payment_methods]
        if self.search:
            params.append(("q", self.search))
        if self.status == "planned":
            params.append(("situacao", "prevista"))
        return urlencode(params)
