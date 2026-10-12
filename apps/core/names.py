"""Normalização determinística de nomes (contas, categorias, tags) — sem LLM.

`normalize_name` gera a chave de comparação: "  MERCADO ", "Mercado" e "mercado" viram "mercado";
"Saúde" vira "saude". O nome de exibição escolhido pelo usuário é preservado à parte.
"""

import unicodedata


class InvalidNameError(ValueError):
    """Nome vazio ou só com espaços."""


def clean_display_name(text: str) -> str:
    """Nome de exibição: Unicode canônico, sem espaços nas pontas nem espaços repetidos."""
    name = " ".join(unicodedata.normalize("NFKC", text).split())
    if not name:
        raise InvalidNameError("Informe um nome.")
    return name


def normalize_name(text: str) -> str:
    """Chave de comparação: sem caixa, sem acentos, espaços colapsados."""
    decomposed = unicodedata.normalize("NFKD", clean_display_name(text).casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))
