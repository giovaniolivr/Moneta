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
from decimal import Decimal, InvalidOperation
from typing import Any, TypeVar

from django.core.exceptions import ValidationError
from django.db import models, transaction

from apps.core.names import InvalidNameError, clean_display_name, normalize_name
from apps.goals.models import Goal
from apps.investments.models import InvestmentProduct
from apps.ledger.models import Account, Transaction
from apps.planning.models import Budget, InstallmentPlan, Recurrence
from apps.taxonomy.models import Category, Tag
from apps.taxonomy.services import add_tag_alias
from apps.users.models import User

from .schema import InvalidFileError, Payload, upgrade

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_ERRORS = 50

_N = TypeVar("_N", Account, Category, Tag, InvestmentProduct)
_R = TypeVar("_R", Account, Category, Tag, Recurrence, InstallmentPlan, InvestmentProduct)
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
    products: Counts = field(default_factory=Counts)
    accounts: Counts = field(default_factory=Counts)
    categories: Counts = field(default_factory=Counts)
    tags: Counts = field(default_factory=Counts)
    recurrences: Counts = field(default_factory=Counts)
    installment_plans: Counts = field(default_factory=Counts)
    budgets: Counts = field(default_factory=Counts)
    goals: Counts = field(default_factory=Counts)
    transactions: Counts = field(default_factory=Counts)

    def sections(self) -> list[tuple[str, Counts]]:
        return [
            ("Produtos de investimento", self.products),
            ("Contas", self.accounts),
            ("Categorias", self.categories),
            ("Tags", self.tags),
            ("Recorrências", self.recurrences),
            ("Compras parceladas", self.installment_plans),
            ("Orçamentos", self.budgets),
            ("Metas", self.goals),
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
        self.products: dict[uuid.UUID, InvestmentProduct] = {}
        self.categories: dict[uuid.UUID, Category] = {}
        self.tags: dict[uuid.UUID, Tag] = {}
        self.recurrences: dict[uuid.UUID, Recurrence] = {}
        self.plans: dict[uuid.UUID, InstallmentPlan] = {}

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
        if value is None and default is None:
            return None  # campo opcional exportado como null
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
        self.each(self.items(payload, "products", "Produto"), self.product)
        self.each(self.items(payload, "accounts", "Conta"), self.account)
        self.each(self.items(payload, "accounts", "Conta"), self.account_billing)
        self.each(self.items(payload, "categories", "Categoria"), self.category)
        self.each(self.items(payload, "tags", "Tag"), self.tag)
        self.each(self.items(payload, "recurrences", "Recorrência"), self.recurrence)
        self.each(
            self.items(payload, "installment_plans", "Compra parcelada"), self.installment_plan
        )
        self.each(self.items(payload, "budgets", "Orçamento"), self.budget)
        self.each(self.items(payload, "goals", "Meta"), self.goal)
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

    def product(self, where: str, item: Item) -> None:
        file_id = self.uuid_field(item, "id", where)
        raw_rate = self.field(item, "rate_percent", where, str, default=None)
        try:
            rate = None if raw_rate is None else Decimal(raw_rate)
        except InvalidOperation:
            raise self.error(where, f"taxa “{raw_rate}” inválida.") from None
        values = {
            "indexer": self.field(item, "indexer", where, str),
            "rate_percent": rate,
            "taxation": self.field(item, "taxation", where, str, default="taxed"),
            "daily_liquidity": self.field(item, "daily_liquidity", where, bool, default=True),
        }
        counts = self.report.products
        self.products[file_id] = self.upsert_named(InvestmentProduct, where, item, values, counts)

    def account_billing(self, where: str, item: Item) -> None:
        """2ª passada: a conta pagadora pode aparecer depois do cartão no arquivo."""
        account = self.accounts.get(self.uuid_field(item, "id", where))
        if account is None:
            return
        values = {
            "statement_closing_day": self.field(
                item, "statement_closing_day", where, int, default=None
            ),
            "payment_due_day": self.field(item, "payment_due_day", where, int, default=None),
            "payment_account": self.ref(
                self.accounts, Account, item, "payment_account", where, "conta pagadora"
            ),
            "product": self.ref(
                self.products, InvestmentProduct, item, "product", where, "produto"
            ),
        }
        if any(getattr(account, k) != v for k, v in values.items()):
            for k, v in values.items():
                setattr(account, k, v)
            try:
                account.full_clean()
            except ValidationError as exc:
                raise self.error(where, _messages(exc)) from None
            account.save()

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
        cache: dict[uuid.UUID, _R],
        model: type[_R],
        item: Item,
        key: str,
        where: str,
        label: str,
    ) -> _R | None:
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

    def entry(self, where: str, item: Item) -> tuple[Item, set[Tag]]:
        """Campos comuns a movimentação e recorrência (tipo, valor, contas, categoria, tags)."""
        account = self.ref(self.accounts, Account, item, "account", where, "conta")
        if account is None:
            raise self.error(where, "campo “account” ausente.")
        tags = set()
        for raw in self.field(item, "tags", where, list, default=[]):
            tag = self.ref(self.tags, Tag, {"tag": raw}, "tag", where, "tag")
            if tag is not None:
                tags.add(tag)
        values: Item = {
            "kind": self.field(item, "kind", where, str),
            "amount_cents": self.field(item, "amount_cents", where, int),
            "account": account,
            "destination_account": self.ref(
                self.accounts, Account, item, "destination_account", where, "conta de destino"
            ),
            "category": self.ref(self.categories, Category, item, "category", where, "categoria"),
            "payment_method": self.field(item, "payment_method", where, str, default=""),
            "description": self.field(item, "description", where, str, default=""),
            "notes": self.field(item, "notes", where, str, default=""),
        }
        return values, tags

    def upsert_by_id(
        self,
        model: type[Transaction] | type[Recurrence] | type[InstallmentPlan],
        pk: uuid.UUID,
        values: Item,
        tags: set[Tag],
        counts: Counts,
        where: str,
    ) -> Any:
        obj = model.objects.for_user(self.user).filter(pk=pk).first()
        created = obj is None
        if obj is None:
            obj = model(id=pk, owner=self.user)
            changed_tags = bool(tags)
        else:
            changed_tags = set(obj.tags.all()) != tags
        changed = any(getattr(obj, k) != v for k, v in values.items())
        for k, v in values.items():
            setattr(obj, k, v)
        self.save(obj, counts, created=created, changed=changed or changed_tags, where=where)
        if created or changed_tags:
            obj.tags.set(tags)
        return obj

    def recurrence(self, where: str, item: Item) -> None:
        pk = self.uuid_field(item, "id", where)
        if self.owned_by_other(Recurrence, pk):
            raise self.error(where, "o identificador pertence a outro usuário deste servidor.")
        values, tags = self.entry(where, item)
        values |= {
            "frequency": self.field(item, "frequency", where, str),
            "interval": self.field(item, "interval", where, int, default=1),
            "start_date": self.date_field(item, "start_date", where),
            "end_date": self.optional_date(item, "end_date", where),
            "is_active": self.field(item, "is_active", where, bool, default=True),
            "generated_through": self.optional_date(item, "generated_through", where),
        }
        obj = self.upsert_by_id(Recurrence, pk, values, tags, self.report.recurrences, where)
        self.recurrences[pk] = obj

    def installment_plan(self, where: str, item: Item) -> None:
        pk = self.uuid_field(item, "id", where)
        if self.owned_by_other(InstallmentPlan, pk):
            raise self.error(where, "o identificador pertence a outro usuário deste servidor.")
        account = self.ref(self.accounts, Account, item, "account", where, "conta")
        category = self.ref(self.categories, Category, item, "category", where, "categoria")
        if account is None or category is None:
            raise self.error(where, "conta e categoria são obrigatórias.")
        tags = {
            tag
            for raw in self.field(item, "tags", where, list, default=[])
            if (tag := self.ref(self.tags, Tag, {"tag": raw}, "tag", where, "tag")) is not None
        }
        values: Item = {
            "description": self.field(item, "description", where, str),
            "total_cents": self.field(item, "total_cents", where, int),
            "count": self.field(item, "count", where, int),
            "purchase_date": self.date_field(item, "purchase_date", where),
            "account": account,
            "category": category,
            "payment_method": self.field(item, "payment_method", where, str, default=""),
            "notes": self.field(item, "notes", where, str, default=""),
        }
        counts = self.report.installment_plans
        self.plans[pk] = self.upsert_by_id(InstallmentPlan, pk, values, tags, counts, where)

    def budget(self, where: str, item: Item) -> None:
        """Upsert pelo UUID; sem ele, o orçamento da mesma categoria (ou do total) é reusado."""
        pk = self.uuid_field(item, "id", where)
        if self.owned_by_other(Budget, pk):
            raise self.error(where, "o identificador pertence a outro usuário deste servidor.")
        category = self.ref(self.categories, Category, item, "category", where, "categoria")
        amount = self.field(item, "amount_cents", where, int)
        own = Budget.objects.for_user(self.user)
        obj = own.filter(pk=pk).first() or own.filter(category=category).first()
        created = obj is None
        if obj is None:
            obj = Budget(id=pk, owner=self.user, category=category)
        changed = obj.amount_cents != amount
        obj.amount_cents = amount
        self.save(obj, self.report.budgets, created=created, changed=changed, where=where)

    def goal(self, where: str, item: Item) -> None:
        """Upsert pelo UUID; sem ele, a meta da mesma conta é reaproveitada."""
        pk = self.uuid_field(item, "id", where)
        if self.owned_by_other(Goal, pk):
            raise self.error(where, "o identificador pertence a outro usuário deste servidor.")
        account = self.ref(self.accounts, Account, item, "account", where, "conta")
        if account is None:
            raise self.error(where, "campo “account” ausente.")
        values: Item = {
            "name": self.field(item, "name", where, str),
            "target_cents": self.field(item, "target_cents", where, int),
            "account": account,
            "deadline": self.optional_date(item, "deadline", where),
        }
        own = Goal.objects.for_user(self.user)
        obj = own.filter(pk=pk).first() or own.filter(account=account).first()
        created = obj is None
        if obj is None:
            obj = Goal(id=pk, owner=self.user)
        changed = any(getattr(obj, k) != v for k, v in values.items())
        for k, v in values.items():
            setattr(obj, k, v)
        self.save(obj, self.report.goals, created=created, changed=changed, where=where)

    def optional_date(self, item: Item, key: str, where: str) -> datetime.date | None:
        return None if item.get(key) is None else self.date_field(item, key, where)

    def movement(self, where: str, item: Item) -> None:
        pk = self.uuid_field(item, "id", where)
        if self.owned_by_other(Transaction, pk):
            raise self.error(where, "o identificador pertence a outro usuário deste servidor.")
        where = f"{where} ({item.get('date', 'sem data')})"
        values, tags = self.entry(where, item)
        values |= {
            "status": self.field(item, "status", where, str, default="posted"),
            "date": self.date_field(item, "date", where),
            "recurrence": self.ref(
                self.recurrences, Recurrence, item, "recurrence", where, "recorrência"
            ),
            "occurrence_date": self.optional_date(item, "occurrence_date", where),
            "installment_plan": self.ref(
                self.plans, InstallmentPlan, item, "installment_plan", where, "compra parcelada"
            ),
            "installment_number": self.field(item, "installment_number", where, int, default=None),
        }
        self.upsert_by_id(Transaction, pk, values, tags, self.report.transactions, where)
