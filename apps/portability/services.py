"""Importação de arquivos do Moneta: upsert por UUID, tudo-ou-nada, idempotente.

Regras:
- Cada objeto é encontrado pelo UUID; se não existir, contas/categorias/tags com o mesmo nome
  (normalizado) são reaproveitadas em vez de duplicadas — as referências do arquivo passam a
  apontar para elas. Sem correspondência, o objeto é criado com o UUID do arquivo.
- O arquivo vence: campos diferentes são atualizados. Importar o mesmo arquivo de novo não muda
  nada (todos "sem alteração").
- UUID que pertence a OUTRO usuário é erro — nunca sobrescreve nem revela dados alheios.
- Nada é gravado se houver qualquer erro; todos os erros são reportados de uma vez.
- Movimentações/categorias que já existiam e não estão no arquivo ficam como estão (importar
  não apaga nada).
"""

import datetime
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from django.core.exceptions import ValidationError
from django.db import models, transaction

from apps.core.names import InvalidNameError, clean_display_name, normalize_name
from apps.ledger.models import Account, Transaction
from apps.taxonomy.models import Category, Tag
from apps.taxonomy.services import add_tag_alias
from apps.users.models import User

from .schema import InvalidFileError, Payload, upgrade

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_ERRORS = 50

_N = TypeVar("_N", Account, Category, Tag)
Item = dict[str, Any]


@dataclass
class Counts:
    created: int = 0
    updated: int = 0
    unchanged: int = 0

    @property
    def total(self) -> int:
        return self.created + self.updated + self.unchanged


@dataclass
class ImportReport:
    accounts: Counts = field(default_factory=Counts)
    categories: Counts = field(default_factory=Counts)
    tags: Counts = field(default_factory=Counts)
    transactions: Counts = field(default_factory=Counts)

    def sections(self) -> list[tuple[str, Counts]]:
        return [
            ("Contas", self.accounts),
            ("Categorias", self.categories),
            ("Tags", self.tags),
            ("Movimentações", self.transactions),
        ]

    @property
    def changes(self) -> int:
        return sum(c.created + c.updated for _, c in self.sections())


class ImportFailedError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__(f"{len(errors)} erro(s) na importação")
        self.errors = errors


def parse_file(raw: bytes) -> Payload:
    """Lê e valida o envelope do arquivo (JSON, formato e versão)."""
    if len(raw) > MAX_FILE_BYTES:
        raise InvalidFileError(f"Arquivo maior que {MAX_FILE_BYTES // (1024 * 1024)} MB.")
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except UnicodeDecodeError, json.JSONDecodeError:
        raise InvalidFileError("O arquivo não é um JSON válido.") from None
    return upgrade(payload)


def import_payload(user: User, payload: Payload, *, dry_run: bool = False) -> ImportReport:
    """Importa (ou só simula, com `dry_run`) um arquivo já validado por `parse_file`."""
    payload = upgrade(payload)
    with transaction.atomic():
        importer = _Importer(user)
        importer.run(payload)
        if importer.errors:
            raise ImportFailedError(importer.errors[:MAX_ERRORS])
        if dry_run:
            transaction.set_rollback(True)
    return importer.report


def _messages(exc: ValidationError) -> str:
    return " ".join(str(m) for m in exc.messages)


class _Skip(Exception):
    """Item inválido: o erro já foi registrado; segue para o próximo (para listar todos)."""


class _Importer:
    def __init__(self, user: User) -> None:
        self.user = user
        self.report = ImportReport()
        self.errors: list[str] = []
        self.accounts: dict[uuid.UUID, Account] = {}
        self.categories: dict[uuid.UUID, Category] = {}
        self.tags: dict[uuid.UUID, Tag] = {}

    # ───── Utilitários ─────

    def error(self, where: str, message: str) -> _Skip:
        self.errors.append(f"{where}: {message}")
        return _Skip()

    def field(self, item: Item, key: str, where: str, kind: type, *, default: Any = ...) -> Any:
        if key not in item:
            if default is ...:
                raise self.error(where, f"campo “{key}” ausente.")
            return default
        value = item[key]
        if kind is int and isinstance(value, bool):
            raise self.error(where, f"campo “{key}” deveria ser um número inteiro.")
        if not isinstance(value, kind):
            raise self.error(where, f"campo “{key}” com tipo inválido.")
        return value

    def uuid_field(self, item: Item, key: str, where: str) -> uuid.UUID:
        raw = self.field(item, key, where, str)
        try:
            return uuid.UUID(raw)
        except ValueError:
            raise self.error(where, f"identificador “{raw}” inválido.") from None

    def date_field(self, item: Item, key: str, where: str) -> datetime.date:
        raw = self.field(item, key, where, str)
        try:
            return datetime.date.fromisoformat(raw)
        except ValueError:
            raise self.error(where, f"data “{raw}” inválida (use AAAA-MM-DD).") from None

    def items(self, payload: Payload, section: str, label: str) -> list[tuple[str, Item]]:
        result = []
        for index, item in enumerate(payload.get(section, []), start=1):
            where = f"{label} {index}"
            if isinstance(item, dict):
                result.append((where, item))
            else:
                self.error(where, "item deveria ser um objeto.")
        return result

    def owned_by_other(self, model: type[models.Model], pk: uuid.UUID) -> bool:
        return model._default_manager.filter(pk=pk).exclude(owner=self.user).exists()

    def save(
        self, obj: models.Model, counts: Counts, *, created: bool, changed: bool, where: str
    ) -> None:
        if not (created or changed):
            counts.unchanged += 1
            return
        try:
            obj.full_clean()
        except ValidationError as exc:
            raise self.error(where, _messages(exc)) from None
        obj.save()
        if created:
            counts.created += 1
        else:
            counts.updated += 1

    def upsert_named(
        self,
        model: type[_N],
        where: str,
        item: Item,
        values: Item,
        counts: Counts,
        **scope: Any,
    ) -> _N:
        """Busca por UUID; senão por nome normalizado (no escopo); senão cria com o UUID."""
        pk = self.uuid_field(item, "id", where)
        if self.owned_by_other(model, pk):
            raise self.error(where, "o identificador pertence a outro usuário deste servidor.")
        try:
            values["name"] = clean_display_name(self.field(item, "name", where, str))
            key = normalize_name(values["name"])
        except InvalidNameError as exc:
            raise self.error(where, str(exc)) from None
        own = model.objects.for_user(self.user)
        obj = own.filter(pk=pk).first() or own.filter(normalized_name=key, **scope).first()
        created = obj is None
        if obj is None:
            obj = model(id=pk, owner=self.user)
        changed = any(getattr(obj, k) != v for k, v in values.items())
        for k, v in values.items():
            setattr(obj, k, v)
        self.save(obj, counts, created=created, changed=changed, where=where)
        return obj

    def each(self, items: list[tuple[str, Item]], fn: Callable[[str, Item], Any]) -> None:
        for where, item in items:
            try:
                fn(where, item)
            except _Skip:
                continue

    # ───── Seções ─────

    def run(self, payload: Payload) -> None:
        self.each(self.items(payload, "accounts", "Conta"), self.account)
        self.each(self.items(payload, "categories", "Categoria"), self.category)
        self.each(self.items(payload, "tags", "Tag"), self.tag)
        self.each(self.items(payload, "transactions", "Movimentação"), self.movement)

    def account(self, where: str, item: Item) -> None:
        file_id = self.uuid_field(item, "id", where)
        values = {
            "kind": self.field(item, "kind", where, str),
            "opening_balance_cents": self.field(item, "opening_balance_cents", where, int),
            "opening_date": self.date_field(item, "opening_date", where),
            "is_archived": self.field(item, "is_archived", where, bool, default=False),
        }
        obj = self.upsert_named(Account, where, item, values, self.report.accounts)
        self.accounts[file_id] = obj

    def category(self, where: str, item: Item) -> None:
        file_id = self.uuid_field(item, "id", where)
        kind = self.field(item, "kind", where, str)
        values = {
            "kind": kind,
            "color": self.field(item, "color", where, str, default="1"),
            "icon": self.field(item, "icon", where, str, default=""),
            "is_archived": self.field(item, "is_archived", where, bool, default=False),
        }
        obj = self.upsert_named(Category, where, item, values, self.report.categories, kind=kind)
        self.categories[file_id] = obj

    def tag(self, where: str, item: Item) -> None:
        file_id = self.uuid_field(item, "id", where)
        aliases = self.field(item, "aliases", where, list, default=[])
        obj = self.upsert_named(Tag, where, item, {}, self.report.tags)
        self.tags[file_id] = obj
        for alias in aliases:
            if not isinstance(alias, str):
                raise self.error(where, "apelido inválido.")
            try:
                add_tag_alias(obj, alias)
            except (ValidationError, InvalidNameError) as exc:
                message = _messages(exc) if isinstance(exc, ValidationError) else str(exc)
                raise self.error(where, f"apelido “{alias}”: {message}") from None

    def ref(
        self,
        cache: dict[uuid.UUID, _N],
        model: type[_N],
        item: Item,
        key: str,
        where: str,
        label: str,
    ) -> _N | None:
        """Objeto referenciado: do próprio arquivo ou já existente do usuário (pelo UUID)."""
        if item.get(key) is None:
            return None
        pk = self.uuid_field(item, key, where)
        if pk not in cache:
            existing = model.objects.for_user(self.user).filter(pk=pk).first()
            if existing is None:
                raise self.error(where, f"{label} “{pk}” não encontrada no arquivo.")
            cache[pk] = existing
        return cache[pk]

    def movement(self, where: str, item: Item) -> None:
        pk = self.uuid_field(item, "id", where)
        if self.owned_by_other(Transaction, pk):
            raise self.error(where, "o identificador pertence a outro usuário deste servidor.")
        where = f"{where} ({item.get('date', 'sem data')})"
        account = self.ref(self.accounts, Account, item, "account", where, "conta")
        if account is None:
            raise self.error(where, "campo “account” ausente.")
        tag_ids = self.field(item, "tags", where, list, default=[])
        tags = set()
        for raw in tag_ids:
            tag = self.ref(self.tags, Tag, {"tag": raw}, "tag", where, "tag")
            if tag is not None:
                tags.add(tag)
        values: Item = {
            "kind": self.field(item, "kind", where, str),
            "status": self.field(item, "status", where, str, default="posted"),
            "amount_cents": self.field(item, "amount_cents", where, int),
            "date": self.date_field(item, "date", where),
            "account": account,
            "destination_account": self.ref(
                self.accounts, Account, item, "destination_account", where, "conta de destino"
            ),
            "category": self.ref(self.categories, Category, item, "category", where, "categoria"),
            "payment_method": self.field(item, "payment_method", where, str, default=""),
            "description": self.field(item, "description", where, str, default=""),
            "notes": self.field(item, "notes", where, str, default=""),
        }
        obj = Transaction.objects.for_user(self.user).filter(pk=pk).first()
        created = obj is None
        if obj is None:
            obj = Transaction(id=pk, owner=self.user)
            changed_tags = bool(tags)
        else:
            changed_tags = set(obj.tags.all()) != tags
        changed = any(getattr(obj, k) != v for k, v in values.items())
        for k, v in values.items():
            setattr(obj, k, v)
        self.save(
            obj,
            self.report.transactions,
            created=created,
            changed=changed or changed_tags,
            where=where,
        )
        if created or changed_tags:
            obj.tags.set(tags)
