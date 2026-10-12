"""Busca de taxas na API pública SGS do Banco Central (sob demanda, nunca automática).

Único acesso à internet do Moneta. Falhas viram `MarketDataError` com mensagem amigável; nada é
gravado pela metade. Valores fora de uma faixa plausível são recusados (proteção contra série
errada ou resposta inesperada).
"""

import datetime
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from .models import MarketRate, Series

# Códigos das séries no SGS (https://www3.bcb.gov.br/sgspub).
SGS_CODES = {
    Series.CDI: 4389,  # Taxa de juros - CDI anualizada base 252 (% a.a.)
    Series.SELIC: 432,  # Meta Selic definida pelo Copom (% a.a.)
    Series.IPCA: 13522,  # IPCA acumulado em 12 meses (%)
}
PLAUSIBLE = (Decimal(-50), Decimal(100))
URL = (
    "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{code}/dados"
    "?formato=json&dataInicial={start:%d/%m/%Y}&dataFinal={end:%d/%m/%Y}"
)
WINDOW_DAYS = 60  # o IPCA é mensal: 60 dias garantem ao menos uma divulgação
TIMEOUT_SECONDS = 10

Fetch = Callable[[str], bytes]


class MarketDataError(Exception):
    pass


ALLOWED_PREFIX = "https://api.bcb.gov.br/"


def _http_get(url: str) -> bytes:
    # Só HTTPS para o host do Banco Central: nada de file:// ou outro destino.
    if not url.startswith(ALLOWED_PREFIX):
        raise MarketDataError("Endereço não permitido.")
    request = urllib.request.Request(url, headers={"Accept": "application/json"})  # noqa: S310
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
        body: bytes = response.read()
        return body


def parse_sgs(raw: bytes) -> list[tuple[datetime.date, Decimal]]:
    try:
        rows = json.loads(raw)
        parsed = [
            (datetime.datetime.strptime(r["data"], "%d/%m/%Y").date(), Decimal(r["valor"]))
            for r in rows
        ]
    except (ValueError, KeyError, TypeError, InvalidOperation) as exc:
        raise MarketDataError("Resposta inesperada do Banco Central.") from exc
    if not parsed:
        raise MarketDataError("O Banco Central não retornou dados.")
    low, high = PLAUSIBLE
    if any(not low < value < high for _, value in parsed):
        raise MarketDataError("O Banco Central retornou um valor fora do esperado.")
    return parsed


def fetch_series(
    series: Series, *, today: datetime.date, fetch: Fetch | None = None
) -> list[tuple[datetime.date, Decimal]]:
    start = today - datetime.timedelta(days=WINDOW_DAYS)
    url = URL.format(code=SGS_CODES[series], start=start, end=today)
    try:
        raw = (fetch or _http_get)(url)  # resolvido na chamada (testes trocam _http_get)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise MarketDataError(
            "Não foi possível falar com o Banco Central agora. Tente mais tarde ou informe a "
            "taxa manualmente."
        ) from exc
    return parse_sgs(raw)


def update_rates(*, fetch: Fetch | None = None) -> dict[Series, MarketRate]:
    """Busca todas as séries e grava (tudo ou nada). Retorna a taxa mais recente de cada uma."""
    today = timezone.localdate()
    fetched = {series: fetch_series(series, today=today, fetch=fetch) for series in SGS_CODES}
    latest: dict[Series, MarketRate] = {}
    with transaction.atomic():
        for series, rows in fetched.items():
            # A Selic meta vem com datas até a próxima reunião do Copom: guarda só até hoje.
            for day, value in (row for row in rows if row[0] <= today):
                rate, _ = MarketRate.objects.update_or_create(
                    series=series, date=day, defaults={"annual_percent": value}
                )
                if series not in latest or day > latest[series].date:
                    latest[series] = rate
    return latest
