"""Leitura para exportação: JSON completo do usuário e CSV de movimentações."""

import csv
import datetime
from collections.abc import Iterable
from typing import IO, Any

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.db.models import QuerySet
from django.utils import timezone

from apps.core.money import cents_to_decimal
from apps.ledger.models import Account, Transaction
from apps.planning.models import Recurrence
from apps.taxonomy.models import Category, Tag

from .schema import FORMAT, SCHEMA_VERSION, Payload

UserLike = AbstractBaseUser | AnonymousUser


def _str(value: Any) -> str | None:
    return None if value is None else str(value)


def _iso(day: datetime.date | None) -> str | None:
    return None if day is None else day.isoformat()


def _entry(obj: Transaction | Recurrence) -> dict[str, Any]:
    return {
        "kind": obj.kind,
        "amount_cents": obj.amount_cents,
        "account": str(obj.account_id),
        "destination_account": _str(obj.destination_account_id),
        "category": _str(obj.category_id),
        "tags": sorted(str(t.pk) for t in obj.tags.all()),
        "payment_method": obj.payment_method,
        "description": obj.description,
        "notes": obj.notes,
    }


def export_payload(user: UserLike) -> Payload:
    """Todos os dados do usuário no formato do Moneta (sem nada que identifique o usuário)."""
    accounts = [
        {
            "id": str(a.pk),
            "name": a.name,
            "kind": a.kind,
            "opening_balance_cents": a.opening_balance_cents,
            "opening_date": a.opening_date.isoformat(),
            "is_archived": a.is_archived,
        }
        for a in Account.objects.for_user(user).order_by("created_at", "pk")
    ]
    categories = [
        {
            "id": str(c.pk),
            "name": c.name,
            "kind": c.kind,
            "color": c.color,
            "icon": c.icon,
            "is_archived": c.is_archived,
        }
        for c in Category.objects.for_user(user).order_by("created_at", "pk")
    ]
    tags = [
        {
            "id": str(t.pk),
            "name": t.name,
            "aliases": sorted(a.normalized_alias for a in t.aliases.all()),
        }
        for t in Tag.objects.for_user(user).prefetch_related("aliases").order_by("created_at", "pk")
    ]
    recurrences = [
        {
            "id": str(r.pk),
            **_entry(r),
            "frequency": r.frequency,
            "interval": r.interval,
            "start_date": r.start_date.isoformat(),
            "end_date": _iso(r.end_date),
            "is_active": r.is_active,
            "generated_through": _iso(r.generated_through),
        }
        for r in Recurrence.objects.for_user(user)
        .prefetch_related("tags")
        .order_by("created_at", "pk")
    ]
    transactions = [
        {
            "id": str(tx.pk),
            **_entry(tx),
            "status": tx.status,
            "date": tx.date.isoformat(),
            "recurrence": _str(tx.recurrence_id),
            "occurrence_date": _iso(tx.occurrence_date),
        }
        for tx in Transaction.objects.for_user(user)
        .prefetch_related("tags")
        .order_by("date", "created_at", "pk")
    ]
    return {
        "format": FORMAT,
        "schema_version": SCHEMA_VERSION,
        "exported_at": timezone.now().isoformat(timespec="seconds"),
        "accounts": accounts,
        "categories": categories,
        "tags": tags,
        "recurrences": recurrences,
        "transactions": transactions,
    }


CSV_HEADER = [
    "Data",
    "Tipo",
    "Valor",
    "Conta",
    "Conta de destino",
    "Categoria",
    "Tags",
    "Forma de pagamento",
    "Descrição",
    "Observações",
]
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _cell(text: str) -> str:
    """Neutraliza injeção de fórmula: planilhas executam células que começam com = + - @."""
    return f"'{text}" if text.startswith(_FORMULA_PREFIXES) else text


def _amount(cents: int) -> str:
    """Formato de planilha pt-BR: vírgula decimal, sem separador de milhar ("1234,56")."""
    return str(cents_to_decimal(cents)).replace(".", ",")


def csv_rows(transactions: QuerySet[Transaction]) -> Iterable[list[str]]:
    qs = transactions.select_related("account", "destination_account", "category")
    for tx in qs.prefetch_related("tags"):
        yield [
            f"{tx.date:%d/%m/%Y}",
            tx.get_kind_display(),
            _amount(tx.amount_cents),
            _cell(tx.account.name),
            _cell(tx.destination_account.name) if tx.destination_account else "",
            _cell(tx.category.name) if tx.category else "",
            _cell(", ".join(sorted(t.name for t in tx.tags.all()))),
            tx.get_payment_method_display() if tx.payment_method else "",
            _cell(tx.description),
            _cell(tx.notes),
        ]


def write_transactions_csv(transactions: QuerySet[Transaction], out: IO[str]) -> None:
    """CSV para Excel/Planilhas em pt-BR: `;` como separador e BOM para acentos corretos.

    "Valor" é sempre positivo, como no app — o efeito vem da coluna "Tipo".
    """
    out.write("﻿")
    writer = csv.writer(out, delimiter=";", lineterminator="\r\n")
    writer.writerow(CSV_HEADER)
    writer.writerows(csv_rows(transactions))


def export_filename(prefix: str, extension: str, today: datetime.date) -> str:
    return f"{prefix}-{today:%Y-%m-%d}.{extension}"
