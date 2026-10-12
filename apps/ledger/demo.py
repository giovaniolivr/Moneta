"""Gerador de dados fictícios para desenvolvimento, testes manuais e capturas de tela.

Simula ~6 meses de uma rotina financeira plausível (salário, aluguel, mercado, delivery, cartão
de crédito com pagamento de fatura, reserva na Caixinha...). Determinístico: a mesma semente e a
mesma data de referência produzem exatamente os mesmos lançamentos.
"""

import datetime
import random
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass

from django.db import transaction

from apps.taxonomy.models import Category
from apps.users.models import User

from .filters import month_bounds
from .models import Account, AccountKind, PaymentMethod, TransactionKind
from .services import TransactionData, create_account, create_transaction, ensure_default_account

DEMO_USERNAME = "demo"
DEMO_PASSWORD = "demo-moneta"  # noqa: S105 — só para o banco local de demonstração


@dataclass(frozen=True)
class DemoSummary:
    accounts: int
    transactions: int
    first_date: datetime.date
    last_date: datetime.date


def _months(today: datetime.date, count: int) -> Iterator[datetime.date]:
    """Primeiro dia de cada um dos últimos `count` meses, do mais antigo ao atual."""
    first = today.replace(day=1)
    months = [first]
    for _ in range(count - 1):
        months.append((months[-1] - datetime.timedelta(days=1)).replace(day=1))
    return reversed(months)


def _cents(rng: random.Random, low: float, high: float) -> int:
    return rng.randint(round(low * 100), round(high * 100))


@transaction.atomic
def seed_demo(
    *, today: datetime.date, months: int = 6, seed: int = 42, username: str = DEMO_USERNAME
) -> DemoSummary:
    rng = random.Random(seed)  # noqa: S311 — dados fictícios, não criptografia
    user = User.objects.create_superuser(username, password=DEMO_PASSWORD, first_name="Ana")
    start = next(_months(today, months))

    checking = ensure_default_account(user)
    checking.name, checking.opening_balance_cents, checking.opening_date = "Nubank", 320_000, start
    checking.save()
    card = create_account(
        user, name="Cartão Nubank", kind=AccountKind.CREDIT_CARD, opening_date=start
    )
    savings = create_account(
        user,
        name="Caixinha Reserva",
        kind=AccountKind.SAVINGS,
        opening_balance_cents=500_000,
        opening_date=start,
    )
    wallet = create_account(
        user,
        name="Carteira",
        kind=AccountKind.CASH,
        opening_balance_cents=15_000,
        opening_date=start,
    )
    cat = {c.name: c for c in Category.objects.for_user(user)}
    card_spend: dict[datetime.date, int] = defaultdict(int)
    count = 0

    def add(
        kind: str,
        cents: int,
        day: datetime.date,
        *,
        account: Account,
        category: str | None = None,
        destination: Account | None = None,
        tags: tuple[str, ...] = (),
        method: str = "",
        description: str = "",
    ) -> None:
        nonlocal count
        if day > today:
            return
        create_transaction(
            user,
            TransactionData(
                kind=kind,
                amount_cents=cents,
                date=day,
                account=account,
                category=cat[category] if category else None,
                destination_account=destination,
                tags=tags,
                payment_method=method,
                description=description,
            ),
        )
        if account == card and kind == TransactionKind.EXPENSE:
            card_spend[day.replace(day=1)] += cents
        count += 1

    def day_in(month: datetime.date, day: int) -> datetime.date:
        return month.replace(day=min(day, month_bounds(month)[1].day))

    def random_day(month: datetime.date) -> datetime.date:
        return day_in(month, rng.randint(1, month_bounds(month)[1].day))

    expense, income, transfer = (
        TransactionKind.EXPENSE,
        TransactionKind.INCOME,
        TransactionKind.TRANSFER,
    )
    pix, credit, debit, boleto, cash = (
        PaymentMethod.PIX,
        PaymentMethod.CREDIT,
        PaymentMethod.DEBIT,
        PaymentMethod.BOLETO,
        PaymentMethod.CASH,
    )

    for month in _months(today, months):
        # Receitas
        add(
            income,
            650_000,
            day_in(month, 5),
            account=checking,
            category="Salário",
            description="Salário",
        )
        if rng.random() < 0.4:
            add(
                income,
                _cents(rng, 800, 1_500),
                random_day(month),
                account=checking,
                category="Outras receitas",
                tags=("Freelance",),
                description="Projeto freelance",
            )
        add(
            income,
            _cents(rng, 38, 46),
            day_in(month, 28),
            account=savings,
            category="Rendimentos",
            description="Rendimento da Caixinha",
        )

        # Contas fixas
        add(
            expense,
            180_000,
            day_in(month, 6),
            account=checking,
            category="Moradia",
            tags=("Aluguel",),
            method=pix,
            description="Aluguel",
        )
        add(
            expense,
            _cents(rng, 140, 230),
            day_in(month, 12),
            account=checking,
            category="Moradia",
            tags=("Energia",),
            method=boleto,
            description="Conta de luz",
        )
        add(
            expense,
            9_990,
            day_in(month, 15),
            account=card,
            category="Assinaturas",
            tags=("Internet",),
            method=credit,
            description="Internet",
        )
        add(
            expense,
            5_590,
            day_in(month, 10),
            account=card,
            category="Assinaturas",
            tags=("Streaming",),
            method=credit,
            description="Streaming",
        )
        add(
            expense,
            2_190,
            day_in(month, 18),
            account=card,
            category="Assinaturas",
            tags=("Música",),
            method=credit,
            description="Música",
        )
        add(
            expense,
            9_990,
            day_in(month, 3),
            account=card,
            category="Saúde",
            tags=("Academia",),
            method=credit,
            description="Academia",
        )
        add(
            expense,
            14_900,
            day_in(month, 20),
            account=card,
            category="Educação",
            tags=("Curso",),
            method=credit,
            description="Curso online",
        )

        # Rotina variável
        for week_day in (2, 9, 16, 23):
            add(
                expense,
                _cents(rng, 180, 420),
                day_in(month, week_day + rng.randint(0, 3)),
                account=card,
                category="Alimentação",
                tags=("Mercado",),
                method=credit,
                description="Mercado",
            )
        for _ in range(rng.randint(2, 5)):
            add(
                expense,
                _cents(rng, 35, 90),
                random_day(month),
                account=card,
                category="Alimentação",
                tags=("Delivery",),
                method=credit,
                description="Delivery",
            )
        for _ in range(rng.randint(3, 6)):
            add(
                expense,
                _cents(rng, 28, 120),
                random_day(month),
                account=checking,
                category="Alimentação",
                tags=("Restaurante",),
                method=rng.choice([pix, debit]),
                description="Almoço",
            )
        for _ in range(rng.randint(6, 12)):
            add(
                expense,
                _cents(rng, 12, 45),
                random_day(month),
                account=card,
                category="Transporte",
                tags=("Uber",),
                method=credit,
            )
        for _ in range(2):
            add(
                expense,
                _cents(rng, 150, 250),
                random_day(month),
                account=card,
                category="Transporte",
                tags=("Combustível",),
                method=credit,
                description="Posto",
            )
        for _ in range(rng.randint(2, 4)):
            tag = rng.choice(["Cinema", "Jogos", "Bar"])
            add(
                expense,
                _cents(rng, 40, 200),
                random_day(month),
                account=card,
                category="Lazer",
                tags=(tag,),
                method=credit,
            )
        if rng.random() < 0.7:
            add(
                expense,
                _cents(rng, 25, 120),
                random_day(month),
                account=wallet,
                category="Saúde",
                tags=("Farmácia",),
                method=cash,
                description="Farmácia",
            )
        for _ in range(rng.randint(1, 3)):
            tag = rng.choice(["Roupas", "Eletrônicos", "Casa"])
            add(
                expense,
                _cents(rng, 60, 600),
                random_day(month),
                account=card,
                category="Compras",
                tags=(tag,),
                method=credit,
            )

        # Reserva e fatura do cartão (transferências: não são receita nem despesa)
        add(
            transfer,
            _cents(rng, 500, 1_500),
            day_in(month, 6),
            account=checking,
            destination=savings,
            description="Reserva do mês",
        )
        add(
            transfer,
            20_000,
            day_in(month, 1),
            account=checking,
            destination=wallet,
            description="Saque",
        )
        previous = (month - datetime.timedelta(days=1)).replace(day=1)
        if card_spend[previous]:
            add(
                transfer,
                card_spend[previous],
                day_in(month, 10),
                account=checking,
                destination=card,
                description="Pagamento da fatura",
            )

    _seed_recurrences(user, cat, today=today, start=start, accounts=(checking, card, savings))
    return DemoSummary(accounts=4, transactions=count, first_date=start, last_date=today)


def _seed_recurrences(
    user: User,
    cat: dict[str, Category],
    *,
    today: datetime.date,
    start: datetime.date,
    accounts: tuple[Account, Account, Account],
) -> None:
    """Contas fixas como recorrências: geram as previstas dos próximos meses."""
    # Import local: o app de planejamento depende do ledger, não o contrário.
    from apps.planning.services import RecurrenceData, create_recurrence

    checking, card, savings = accounts
    fixed = [
        ("income", 650_000, 5, checking, "Salário", "Salário", "", None),
        ("expense", 180_000, 6, checking, "Moradia", "Aluguel", PaymentMethod.PIX, None),
        ("expense", 9_990, 15, card, "Assinaturas", "Internet", PaymentMethod.CREDIT, None),
        ("expense", 5_590, 10, card, "Assinaturas", "Streaming", PaymentMethod.CREDIT, None),
        ("transfer", 50_000, 7, checking, None, "Reserva mensal", "", savings),
    ]
    for kind, cents, day, account, category, description, method, destination in fixed:
        create_recurrence(
            user,
            RecurrenceData(
                entry=TransactionData(
                    kind=kind,
                    amount_cents=cents,
                    date=start,
                    account=account,
                    category=cat[category] if category else None,
                    destination_account=destination,
                    payment_method=method,
                    description=description,
                ),
                frequency="monthly",
                interval=1,
                start_date=start.replace(day=day),
            ),
            today=today,
        )
