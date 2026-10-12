import datetime
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.db.models import QuerySet

from apps.analytics import selectors as analytics
from apps.analytics.domain.metrics import format_pct, percent
from apps.analytics.domain.periods import add_months, first_full_month, month_start
from apps.core.money import format_brl
from apps.ledger.models import Account, AccountKind
from apps.ledger.selectors import account_balances

from .domain.products import Indexer, MissingRateError, ProductSpec, Taxation
from .domain.purchase import (
    Comparison,
    Payment,
    SavingPlan,
    Strategy,
    compare,
    installments,
    monthly_to_reach,
    months_to_save,
    split_installments,
)
from .domain.rates import Prefixed, Yield
from .domain.simulation import Simulation, simulate
from .domain.taxes import EXEMPT, TaxRule
from .models import InvestmentProduct, MarketRate, RateOverride, Series

UserLike = AbstractBaseUser | AnonymousUser


@dataclass(frozen=True)
class EffectiveRate:
    series: Series
    published: MarketRate | None  # último valor do Banco Central
    manual: Decimal | None  # valor informado pelo usuário (tem prioridade)

    @property
    def annual_percent(self) -> Decimal | None:
        if self.manual is not None:
            return self.manual
        return self.published.annual_percent if self.published else None

    @property
    def annual(self) -> Decimal | None:
        """Como fração (0.149) para o núcleo de cálculo."""
        value = self.annual_percent
        return value / 100 if value is not None else None

    @property
    def source(self) -> str:
        if self.manual is not None:
            return "informada por você"
        if self.published is not None:
            return f"Banco Central, {self.published.date:%d/%m/%Y}"
        return "sem dado"

    def is_stale(self, today: datetime.date) -> bool:
        # O IPCA é mensal e sai com ~1 mês de defasagem; CDI e Selic são diários.
        days = 62 if self.series == Series.IPCA else 7
        return self.manual is None and (
            self.published is None or (today - self.published.date).days > days
        )


def effective_rates(user: UserLike) -> dict[Series, EffectiveRate]:
    manual = dict(RateOverride.objects.for_user(user).values_list("series", "annual_percent"))
    result = {}
    for series in Series:
        published = MarketRate.objects.filter(series=series).order_by("-date").first()
        result[series] = EffectiveRate(series, published, manual.get(series))
    return result


@dataclass(frozen=True)
class ProductRow:
    product: InvestmentProduct
    gross_year: Decimal | None  # rentabilidade em 1 ano, fração
    net_year: Decimal | None
    missing: str  # taxa de mercado que falta para estimar ("CDI", "Selic") ou ""
    accounts: list[str]


def list_products(user: UserLike) -> QuerySet[InvestmentProduct]:
    return InvestmentProduct.objects.for_user(user)


def product_rows(user: UserLike, today: datetime.date) -> list[ProductRow]:
    rates = effective_rates(user)
    cdi, selic = rates[Series.CDI].annual, rates[Series.SELIC].annual
    rows = []
    for product in list_products(user).prefetch_related("accounts"):
        gross: Decimal | None = None
        net: Decimal | None = None
        missing = ""
        try:
            gross, net = product.spec.one_year(cdi=cdi, selic=selic, start=today)
        except MissingRateError as exc:
            missing = str(exc)
        accounts = [a.name for a in product.accounts.all() if a.owner_id == product.owner_id]
        rows.append(ProductRow(product, gross, net, missing, accounts))
    return rows


REFERENCES: dict[str, tuple[str, ProductSpec]] = {
    "ref-cdi": ("100% do CDI (referência)", ProductSpec(Indexer.CDI, Decimal(100), Taxation.TAXED)),
    "ref-poupanca": ("Poupança (referência)", ProductSpec(Indexer.POUPANCA, None, Taxation.EXEMPT)),
}


@dataclass(frozen=True)
class SimulationOption:
    key: str
    label: str
    spec: ProductSpec


def simulation_options(user: UserLike) -> list[SimulationOption]:
    """Produtos do usuário + referências fixas (para simular mesmo sem produto cadastrado)."""
    own = [
        SimulationOption(str(p.pk), f"{p.name} ({p.rate_label})", p.spec)
        for p in list_products(user)
    ]
    refs = [SimulationOption(key, label, spec) for key, (label, spec) in REFERENCES.items()]
    return own + refs


@dataclass(frozen=True)
class SimulationRow:
    option: SimulationOption
    simulation: Simulation | None
    missing: str  # taxa de mercado que faltou
    real_net_cents: int | None  # líquido em dinheiro de hoje (IPCA)


def run_simulations(
    user: UserLike,
    options: list[SimulationOption],
    *,
    start: datetime.date,
    initial_cents: int,
    monthly_cents: int,
    months: int,
) -> list[SimulationRow]:
    rates = effective_rates(user)
    cdi, selic = rates[Series.CDI].annual, rates[Series.SELIC].annual
    ipca = rates[Series.IPCA].annual
    rows = []
    for option in options:
        try:
            yield_ = option.spec.yield_for(cdi=cdi, selic=selic)
        except MissingRateError as exc:
            rows.append(SimulationRow(option, None, str(exc), None))
            continue
        result = simulate(
            yield_=yield_,
            taxes=option.spec.taxation.rule,
            start=start,
            initial_cents=initial_cents,
            monthly_cents=monthly_cents,
            months=months,
        )
        rows.append(SimulationRow(option, result, "", result.real_value(ipca)))
    return rows


def offered_strategies(data: dict[str, Any], start: datetime.date) -> list[Strategy]:
    """Estratégias a partir das ofertas do formulário (à vista sempre entra)."""
    price: int = data["valor"]
    discount: Decimal = data.get("desconto") or Decimal(0)
    cash_value = round(price * (1 - discount / 100))
    label = f"À vista com {format_pct(discount)} de desconto" if discount else "À vista"
    strategies = [Strategy("avista", label, (Payment(start, cash_value),))]
    if n := data.get("sj_parcelas"):
        first = 0 if data.get("sj_no_ato") else 1
        strategies.append(
            Strategy(
                "sem-juros",
                f"{n}x sem juros",
                tuple(split_installments(start, price, n, first_in_months=first)),
            )
        )
    if (n := data.get("cj_parcelas")) and data.get("cj_valor"):
        strategies.append(
            Strategy(
                "com-juros",
                f"{n}x de {format_brl(data['cj_valor'])}",
                tuple(installments(start, n, data["cj_valor"])),
            )
        )
    if (n := data.get("en_parcelas")) and data.get("en_valor"):
        down = data.get("en_entrada") or 0
        payments = [Payment(start, down)] if down else []
        payments += installments(start, n, data["en_valor"])
        strategies.append(
            Strategy(
                "entrada",
                f"Entrada de {format_brl(down)} + {n}x de {format_brl(data['en_valor'])}",
                tuple(payments),
            )
        )
    return strategies


@dataclass(frozen=True)
class PurchaseAnalysis:
    comparison: Comparison
    option_label: str
    explanation: str


def _money_source(user: UserLike, data: dict[str, Any]) -> tuple[Yield, TaxRule, str]:
    """Rendimento e tributação de onde o dinheiro está (MissingRateError se faltar taxa)."""
    rates = effective_rates(user)
    cdi, selic = rates[Series.CDI].annual, rates[Series.SELIC].annual
    option = {o.key: o for o in simulation_options(user)}.get(data["produto"])
    if option is None:
        return Prefixed(Decimal(0)), EXEMPT, "parado (sem rendimento)"
    return option.spec.yield_for(cdi=cdi, selic=selic), option.spec.taxation.rule, option.label


def analyze_purchase(
    user: UserLike, data: dict[str, Any], *, start: datetime.date
) -> PurchaseAnalysis:
    """Compara as ofertas com o dinheiro aplicado onde ele está hoje."""
    yield_, taxes, label = _money_source(user, data)
    strategies = offered_strategies(data, start)
    cash_price = strategies[0].payments[0].cents
    comparison = compare(
        strategies, yield_=yield_, taxes=taxes, start=start, cash_price_cents=cash_price
    )
    return PurchaseAnalysis(comparison, label, explain(comparison, label))


def explain(comparison: Comparison, money_where: str) -> str:
    """Frase com a recomendação e o porquê (sempre com números)."""
    best = comparison.best
    if len(comparison.results) == 1:
        return f"{best.strategy.label} custa {format_brl(best.cost_cents)} em dinheiro de hoje."
    runner_up = comparison.results[1]
    text = (
        f"{best.strategy.label} é a opção que menos pesa: custa {format_brl(best.cost_cents)} "
        f"em dinheiro de hoje, {format_brl(runner_up.extra_vs_best_cents)} a menos que "
        f"{runner_up.strategy.label.lower()}"
    )
    if best.strategy.key == "avista" and "desconto" in best.strategy.label:
        reason = "o desconto vale mais do que o dinheiro renderia esperando as parcelas"
    elif best.strategy.key == "avista":
        reason = "as outras ofertas cobram mais do que o seu dinheiro rende"
    elif best.strategy.key == "sem-juros" and "sem rendimento" not in money_where:
        reason = f"o dinheiro continua rendendo ({money_where}) até cada parcela vencer"
    elif best.strategy.key == "sem-juros":
        reason = "o custo é o mesmo, mas o desembolso se espalha pelos meses"
    else:
        reason = "considerando o rendimento do dinheiro até cada pagamento"
    text += f" — {reason}."
    for r in comparison.results:
        if r.implied_rate is None:
            continue
        monthly = format_pct((r.implied_rate * Decimal(100)).quantize(Decimal("0.01")))
        text += f" {r.strategy.label} embute juros de {monthly} ao mês sobre o preço à vista."
    return text


# Cabe no bolso? Juntar antes de comprar -------------------------------------------

CAPACITY_MONTHS = 3


@dataclass(frozen=True)
class Pocket:
    """Capacidade de poupança (resultado médio dos meses fechados completos) e saldo em
    contas hoje (sem cartões)."""

    capacity_cents: int | None
    capacity_months: int
    balance_cents: int


def pocket(user: UserLike, today: datetime.date) -> Pocket:
    current = month_start(today)
    first = analytics.first_activity_date(user)
    results: list[int] = []
    if first is not None:
        since = max(first_full_month(first), add_months(current, -CAPACITY_MONTHS))
        if since < current:
            series = analytics.monthly_series(user, since, current - datetime.timedelta(1))
            results = [p.net_cents for p in series]
    balances = account_balances(user, on=today)
    in_accounts = sum(
        balances.get(a.pk, 0)
        for a in Account.objects.for_user(user).exclude(kind=AccountKind.CREDIT_CARD)
    )
    capacity = round(sum(results) / len(results)) if results else None
    return Pocket(capacity, len(results), in_accounts)


@dataclass(frozen=True)
class Fit:
    strategy: Strategy
    verdict: str  # "cabe", "aperta", "sem base"
    detail: str


def fits(comparison: Comparison, money: Pocket) -> list[Fit]:
    """Cada forma de pagar contra a capacidade mensal e o saldo em contas (com números)."""
    result = []
    for r in comparison.results:
        s = r.strategy
        if s.months_committed == 1:
            share = percent(s.total_cents, money.balance_cents) if money.balance_cents > 0 else None
            if money.balance_cents >= s.total_cents:
                verdict, detail = "cabe", f"usa {format_pct(share)} do saldo em contas hoje"
            else:
                verdict = "aperta"
                detail = f"o saldo em contas hoje é {format_brl(money.balance_cents)}"
        elif money.capacity_cents is None:
            verdict, detail = "sem base", "ainda sem meses fechados para medir sua capacidade"
        elif s.max_month_cents <= money.capacity_cents:
            verdict = "cabe"
            detail = (
                f"{format_brl(s.max_month_cents)}/mês de {format_brl(money.capacity_cents)} que "
                "você costuma guardar"
            )
        else:
            verdict = "aperta"
            detail = (
                f"{format_brl(s.max_month_cents)}/mês, acima dos "
                f"{format_brl(max(money.capacity_cents, 0))} que você costuma guardar"
            )
        result.append(Fit(s, verdict, detail))
    return result


@dataclass(frozen=True)
class SavingAnalysis:
    plan: SavingPlan | None  # juntando `monthly` por mês
    monthly_cents: int | None
    needed_monthly_cents: int | None  # para comprar até `deadline`
    deadline: datetime.date | None
    target_cents: int  # preço à vista (com desconto)
    vs_installments: list[tuple[Strategy, int]]  # (estratégia, quanto juntar economiza)


def saving_analysis(
    user: UserLike, data: dict[str, Any], comparison: Comparison, *, start: datetime.date
) -> SavingAnalysis | None:
    monthly: int | None = data.get("guardar")
    deadline: datetime.date | None = data.get("ate")
    if not monthly and not deadline:
        return None
    yield_, taxes, _ = _money_source(user, data)
    cash = next(r.strategy for r in comparison.results if r.strategy.key == "avista")
    target = cash.total_cents
    plan = (
        months_to_save(
            yield_=yield_, taxes=taxes, start=start, monthly_cents=monthly, target_cents=target
        )
        if monthly
        else None
    )
    needed = (
        monthly_to_reach(
            yield_=yield_, taxes=taxes, start=start, target_cents=target, deadline=deadline
        )
        if deadline
        else None
    )
    deposited = plan.deposited_cents if plan else None
    vs = [
        (r.strategy, r.strategy.total_cents - deposited)
        for r in comparison.results
        if deposited is not None and r.strategy.key != "avista"
    ]
    return SavingAnalysis(plan, monthly, needed, deadline, target, vs)
