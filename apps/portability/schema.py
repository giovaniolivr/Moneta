"""Formato do arquivo de exportação do Moneta (JSON próprio, não `dumpdata`).

    {"format": "moneta", "schema_version": 1, "exported_at": "...",
     "accounts": [...], "categories": [...], "tags": [...], "transactions": [...]}

Referências entre objetos usam os UUIDs. Para mudar o formato: incremente `SCHEMA_VERSION` e
registre em `MIGRATIONS[v]` a função que converte um arquivo da versão `v` para `v + 1` — arquivos
antigos continuam importáveis.
"""

from collections.abc import Callable
from typing import Any

FORMAT = "moneta"
SCHEMA_VERSION = 3

Payload = dict[str, Any]


def _v1_to_v2(payload: Payload) -> Payload:
    """v2 (Fase 2): movimentações ganham `status`; tudo que existia na v1 era realizado."""
    for tx in payload.get("transactions", []):
        if isinstance(tx, dict):
            tx.setdefault("status", "posted")
    return payload


def _v2_to_v3(payload: Payload) -> Payload:
    """v3: seção `recurrences`; movimentações podem apontar para a recorrência de origem."""
    payload.setdefault("recurrences", [])
    return payload


MIGRATIONS: dict[int, Callable[[Payload], Payload]] = {1: _v1_to_v2, 2: _v2_to_v3}
SECTIONS = ("accounts", "categories", "tags", "recurrences", "transactions")


class InvalidFileError(Exception):
    """O arquivo não é uma exportação do Moneta utilizável."""


def upgrade(raw: Any) -> Payload:
    """Valida o envelope e converte arquivos antigos para a versão atual do formato."""
    if not isinstance(raw, dict) or raw.get("format") != FORMAT:
        raise InvalidFileError("O arquivo não é uma exportação do Moneta.")
    payload: Payload = raw
    version = payload.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise InvalidFileError("Versão do formato ausente ou inválida.")
    if version > SCHEMA_VERSION:
        raise InvalidFileError(
            f"O arquivo usa a versão {version} do formato, mais nova que a suportada "
            f"({SCHEMA_VERSION}). Atualize o Moneta antes de importar."
        )
    while version < SCHEMA_VERSION:
        payload = MIGRATIONS[version](payload)
        version += 1
        payload["schema_version"] = version
    for section in SECTIONS:
        if not isinstance(payload.get(section, []), list):
            raise InvalidFileError(f"A seção “{section}” deveria ser uma lista.")
    return payload
