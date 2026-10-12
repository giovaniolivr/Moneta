"""Análises automáticas (Python puro, sem Django).

Cada regra recebe um `AnalyticsContext` já agregado e devolve zero ou mais `Insight`s. Todo
insight carrega a evidência numérica usada (mostrada no "por quê?" e verificada nos testes) e o
filtro das movimentações que o geraram (drill-down).

Princípios:
- Sem histórico suficiente, a regra devolve um insight `insufficient=True` dizendo o que falta,
  em vez de concluir.
- Mês em andamento nunca é comparado com meses fechados inteiros: a base histórica usa os mesmos
  dias de cada mês (pro-rata), como em `periods.comparable_ranges`.
- Só meses COMPLETOS servem de histórico (o primeiro mês de uso costuma estar pela metade).
- Limiares relativos sempre acompanhados de um mínimo em reais (R$ 3 → R$ 5 não é alerta).
"""

import datetime
import itertools
import statistics
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal, Protocol

from apps.core.money import format_brl
from apps.ledger.filters import TransactionFilter

from .metrics import Totals, format_pct, pct_change, percent
from .periods import DateRange, month_end

Severity = Literal["info", "attention", "positive"]
Unit = Literal["brl", "pct", "text"]

EXPENSE = "expense"
INCOME = "income"
MONTH_NAMES = [
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
]
# (singular, plural com artigo) por `date.weekday()`.
WEEKDAYS = [
    ("segunda-feira", "às segundas-feiras"),
    ("terça-feira", "às terças-feiras"),
    ("quarta-feira", "às quartas-feiras"),
    ("quinta-feira", "às quintas-feiras"),
    ("sexta-feira", "às sextas-feiras"),
    ("sábado", "aos sábados"),
    ("domingo", "aos domingos"),
]


@dataclass(frozen=True)
class Evidence:
    label: str
    value: int | Decimal | str | None
    unit: Unit = "brl"


@dataclass(frozen=True)
class Insight:
    rule_id: str
    title: str
    severity: Severity
    message: str
    evidence: tuple[Evidence, ...] = ()
    filter: TransactionFilter | None = None
    insufficient: bool = False
    url: str = ""  # drill-down fora das movimentações (ex.: item da lista de desejos)


@dataclass(frozen=True)
class InsightSettings:
    """Limiares das regras (valores em centavos; percentuais em pontos)."""

    increase_pct: Decimal = Decimal(15)
    increase_min_cents: int = 5_000
    baseline_months: int = 3
    min_elapsed_days: int = 7  # mês em andamento: antes disso a comparação é ruído
    trend_steps: int = 3  # altas/quedas consecutivas
    trend_min_cents: int = 5_000
    concentration_pct: Decimal = Decimal(50)
    concentration_max_items: int = 3
    concentration_min_lift: Decimal = Decimal("1.5")  # vs. a fatia "justa" (k de n categorias)
    weekday_weeks: int = 8
    weekday_ratio: Decimal = Decimal("1.5")
    weekday_min_cents: int = 2_000
    max_per_rule: int = 3


@dataclass(frozen=True)
class MonthData:
    """Um mês fechado e completo do histórico."""

    month: datetime.date
    income_cents: int
    expense_cents: int
    by_category: Mapping[uuid.UUID, int]  # despesas do mês inteiro
    comparable_by_category: Mapping[uuid.UUID, int]  # só os mesmos dias do recorte atual


@dataclass(frozen=True)
class BudgetSignal:
    """Andamento de um orçamento no mês (calculado fora; aqui só vira análise)."""

    label: str
    category_id: uuid.UUID | None  # None = todas as despesas
    limit_cents: int
    spent_cents: int
    expected_cents: int  # quanto "deveria" estar gasto até hoje
    projected_cents: int | None
    status: Literal["ok", "pace", "over"]
    method: Literal["history", "linear", "closed"]


@dataclass(frozen=True)
class PriceSignal:
    """Preço bom agora num item da lista de desejos (calculado fora; aqui vira análise)."""

    item: str
    url: str
    store: str
    current_cents: int
    kind: Literal["target", "low"]
    target_cents: int | None = None
    previous_low_cents: int | None = None
    window_days: int = 60


@dataclass(frozen=True)
class AnalyticsContext:
    today: datetime.date
    current: DateRange  # recorte efetivo (até hoje)
    partial: bool
    single_month: bool
    totals: Totals
    previous: DateRange | None
    previous_totals: Totals | None
    expenses_by_category: Mapping[uuid.UUID, int]
    category_names: Mapping[uuid.UUID, str]
    months: Sequence[MonthData]  # meses completos fechados até o fim do recorte, mais antigo 1º
    first_date: datetime.date | None  # primeira receita/despesa do usuário
    weekday_window: DateRange | None = None
    daily_expenses: Mapping[datetime.date, int] = field(default_factory=dict)
    budgets: Sequence[BudgetSignal] = ()
    prices: Sequence[PriceSignal] = ()
    settings: InsightSettings = field(default_factory=InsightSettings)

    @property
    def has_data(self) -> bool:
        return bool(self.totals.income_cents or self.totals.expense_cents)

    def name(self, category_id: uuid.UUID) -> str:
        return self.category_names.get(category_id, "Categoria removida")


class InsightRule(Protocol):
    id: str
    title: str

    def evaluate(self, ctx: AnalyticsContext) -> list[Insight]: ...


# ───── Formatação ─────


def month_name(day: datetime.date) -> str:
    return MONTH_NAMES[day.month - 1]


def _brl(cents: int) -> str:
    return format_brl(cents)


def _date(day: datetime.date) -> str:
    return f"{day:%d/%m/%Y}"


def _join(names: Sequence[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} e {names[-1]}"


def _period_phrase(ctx: AnalyticsContext) -> str:
    if ctx.single_month:
        name = month_name(ctx.current.start)
        return f"em {name} até o dia {ctx.current.end.day}" if ctx.partial else f"em {name}"
    return f"de {_date(ctx.current.start)} a {_date(ctx.current.end)}"


def _expense_filter(span: DateRange, category_ids: Sequence[uuid.UUID] = ()) -> TransactionFilter:
    return TransactionFilter(
        date_from=span.start,
        date_to=span.end,
        kinds=(EXPENSE,),
        category_ids=tuple(category_ids),
    )


def _insufficient(rule: InsightRule, message: str) -> list[Insight]:
    return [Insight(rule.id, rule.title, "info", message, insufficient=True)]


# ───── Regras ─────


class MonthSummaryRule:
    id = "resumo"
    title = "Resumo"

    def evaluate(self, ctx: AnalyticsContext) -> list[Insight]:
        if not ctx.has_data:
            return []
        t = ctx.totals
        parts = [f"{_period_phrase(ctx).capitalize()}, você gastou {_brl(t.expense_cents)}"]
        evidence = [
            Evidence("Receitas", t.income_cents),
            Evidence("Despesas", t.expense_cents),
            Evidence("Resultado", t.net_cents),
        ]
        change = None
        if ctx.previous and ctx.previous_totals:
            prev = ctx.previous_totals.expense_cents
            change = pct_change(t.expense_cents, prev)
            evidence.append(
                Evidence(
                    f"Despesas de {_date(ctx.previous.start)} a {_date(ctx.previous.end)}", prev
                )
            )
            if change is not None and change != 0:
                direction = "mais" if change > 0 else "menos"
                since = _previous_phrase(ctx, ctx.previous)
                parts[0] += f", {format_pct(abs(change))} a {direction} que {since}"
                evidence.append(Evidence("Variação das despesas", change, "pct"))
        if t.net_cents > 0:
            parts.append(
                f"Sobraram {_brl(t.net_cents)} ({format_pct(t.savings_rate)} das receitas)"
            )
            evidence.append(Evidence("Taxa de poupança", t.savings_rate, "pct"))
        elif t.net_cents < 0:
            verb = "Até agora, as despesas superam" if ctx.partial else "As despesas superaram"
            parts.append(f"{verb} as receitas em {_brl(-t.net_cents)}")
        else:
            parts.append("Receitas e despesas empataram")

        # Mês em andamento no vermelho é comum (antes do salário): só informa.
        if t.net_cents < 0:
            severity: Severity = "info" if ctx.partial else "attention"
        elif t.net_cents > 0 and (change is None or change <= 0):
            severity = "positive"
        else:
            severity = "info"
        return [
            Insight(
                self.id,
                self.title,
                severity,
                ". ".join(parts) + ".",
                tuple(evidence),
                TransactionFilter(
                    date_from=ctx.current.start, date_to=ctx.current.end, kinds=(INCOME, EXPENSE)
                ),
            )
        ]


def _previous_phrase(ctx: AnalyticsContext, previous: DateRange) -> str:
    if ctx.single_month:
        name = month_name(previous.start)
        return f"nos mesmos dias de {name}" if ctx.partial else f"em {name}"
    return "no mesmo número de dias do período anterior" if ctx.partial else "no período anterior"


class ConcentrationRule:
    """Poucas categorias respondem pela maior parte das despesas."""

    id = "concentracao"
    title = "Concentração"

    def evaluate(self, ctx: AnalyticsContext) -> list[Insight]:
        s = ctx.settings
        ranked = sorted(ctx.expenses_by_category.items(), key=lambda kv: kv[1], reverse=True)
        total = sum(cents for _, cents in ranked)
        if total == 0 or len(ranked) < 2:
            return []
        size, share = self._smallest_majority(ranked, total, s)
        if share is None:
            return []
        fair_share = Decimal(size * 100) / len(ranked)
        if size == len(ranked) or share < fair_share * s.concentration_min_lift:
            return []
        top = ranked[:size]
        names = [ctx.name(cid) for cid, _ in top]
        subject = f"{names[0]} sozinha responde por" if size == 1 else f"{_join(names)} concentram"
        evidence = [Evidence(ctx.name(cid), cents) for cid, cents in top]
        evidence += [
            Evidence("Total das despesas", total),
            Evidence(f"Participação {'da categoria' if size == 1 else 'somada'}", share, "pct"),
            Evidence("Categorias com despesas", str(len(ranked)), "text"),
        ]
        return [
            Insight(
                self.id,
                self.title,
                "info",
                f"{subject} {format_pct(share)} das despesas {_period_phrase(ctx)}.",
                tuple(evidence),
                _expense_filter(ctx.current, [cid for cid, _ in top]),
            )
        ]

    @staticmethod
    def _smallest_majority(
        ranked: Sequence[tuple[uuid.UUID, int]], total: int, s: InsightSettings
    ) -> tuple[int, Decimal | None]:
        """Menor quantidade de categorias (até o limite) que atinge a participação mínima."""
        running = 0
        for size, (_, cents) in enumerate(ranked[: s.concentration_max_items], start=1):
            running += cents
            share = percent(running, total)
            if share is not None and share >= s.concentration_pct:
                return size, share
        return 0, None


class AboveAverageRule:
    """Categoria do mês acima da média dos meses anteriores (mesmos dias, se em andamento)."""

    id = "acima_da_media"
    title = "Acima da média"

    def evaluate(self, ctx: AnalyticsContext) -> list[Insight]:
        if not ctx.single_month or not ctx.expenses_by_category:
            return []
        s = ctx.settings
        baseline = [m for m in ctx.months if m.month < ctx.current.start][-s.baseline_months :]
        if len(baseline) < s.baseline_months:
            return _insufficient(
                self,
                f"Comparar categorias com a média exige {s.baseline_months} meses completos de "
                f"histórico antes de {month_name(ctx.current.start)} (há {len(baseline)}).",
            )
        if ctx.partial and ctx.current.days < s.min_elapsed_days:
            return _insufficient(
                self,
                f"A comparação de categorias com a média começa no {s.min_elapsed_days}º dia do "
                "mês; antes disso, poucos lançamentos distorcem o resultado.",
            )
        base_label = (
            f"média dos mesmos dias ({ctx.current.start.day} a {ctx.current.end.day}) "
            f"nos {len(baseline)} meses anteriores"
            if ctx.partial
            else f"média dos {len(baseline)} meses anteriores"
        )
        found: list[tuple[int, Insight]] = []
        for cid, cents in ctx.expenses_by_category.items():
            values = [
                (m.comparable_by_category if ctx.partial else m.by_category).get(cid, 0)
                for m in baseline
            ]
            average = round(sum(values) / len(values))
            change = pct_change(cents, average)
            if (
                average == 0
                or change is None
                or change < s.increase_pct
                or cents - average < s.increase_min_cents
            ):
                continue
            name = ctx.name(cid)
            evidence = [Evidence(f"{name} {_period_phrase(ctx)}", cents)]
            evidence += [
                Evidence(month_name(m.month).capitalize(), v)
                for m, v in zip(baseline, values, strict=True)
            ]
            evidence += [
                Evidence(base_label.capitalize(), average),
                Evidence("Diferença", cents - average),
                Evidence("Variação", change, "pct"),
            ]
            message = (
                f"{name}: {_brl(cents)} {_period_phrase(ctx)}, {format_pct(change)} acima da "
                f"{base_label} ({_brl(average)})."
            )
            insight = Insight(
                self.id,
                self.title,
                "attention",
                message,
                tuple(evidence),
                _expense_filter(ctx.current, [cid]),
            )
            found.append((cents - average, insight))
        found.sort(key=lambda item: item[0], reverse=True)
        return [insight for _, insight in found[: s.max_per_rule]]


def _monotonic(values: Sequence[int]) -> int:
    """1 se estritamente crescente, -1 se estritamente decrescente, 0 caso contrário."""
    pairs = list(itertools.pairwise(values))
    if all(b > a for a, b in pairs):
        return 1
    if all(b < a for a, b in pairs):
        return -1
    return 0


class TrendRule:
    """Despesas (total ou por categoria) subindo/caindo em meses fechados consecutivos."""

    id = "tendencia"
    title = "Tendência"

    def evaluate(self, ctx: AnalyticsContext) -> list[Insight]:
        s = ctx.settings
        needed = s.trend_steps + 1
        window = list(ctx.months[-needed:])
        if len(window) < needed:
            return _insufficient(
                self,
                f"Identificar tendências exige {needed} meses completos de histórico "
                f"(há {len(window)}).",
            )
        span = DateRange(window[0].month, month_end(window[-1].month))
        first, last = month_name(window[0].month), month_name(window[-1].month)

        series: list[tuple[str, list[int], tuple[uuid.UUID, ...]]] = [
            ("Suas despesas", [m.expense_cents for m in window], ())
        ]
        category_ids = {cid for m in window for cid in m.by_category}
        series += [
            (ctx.name(cid), [m.by_category.get(cid, 0) for m in window], (cid,))
            for cid in sorted(category_ids, key=lambda c: ctx.name(c))
        ]
        found: list[tuple[int, Insight]] = []
        for subject, values, ids in series:
            direction = _monotonic(values)
            change = values[-1] - values[0]
            if direction == 0 or abs(change) < s.trend_min_cents:
                continue
            if ids:
                subject = f"Os gastos com {subject}"
            change_pct = pct_change(values[-1], values[0])
            pct_text = (
                f" ({'+' if change > 0 else ''}{format_pct(change_pct)})"
                if change_pct is not None
                else ""
            )
            message = (
                f"{subject} {'subiram' if direction > 0 else 'caíram'} {s.trend_steps} meses "
                f"seguidos: de {_brl(values[0])} em {first} para {_brl(values[-1])} em "
                f"{last}{pct_text}."
            )
            evidence = [
                Evidence(f"{month_name(m.month).capitalize()}/{m.month:%y}", v)
                for m, v in zip(window, values, strict=True)
            ]
            evidence.append(Evidence("Variação no período", change_pct, "pct"))
            insight = Insight(
                self.id,
                self.title,
                "attention" if direction > 0 else "positive",
                message,
                tuple(evidence),
                _expense_filter(span, ids),
            )
            # O total vem antes das categorias; entre categorias, a maior variação primeiro.
            found.append((0 if not ids else abs(change), insight))
        found.sort(key=lambda item: (item[0] != 0, -item[0]))
        return [insight for _, insight in found[: s.max_per_rule]]


class WeekdayRule:
    """Dia da semana em que o gasto típico é bem maior que nos demais.

    Usa a mediana dos totais diários: um gasto grande isolado (aluguel, uma compra única) não
    transforma o dia em que caiu num "padrão".
    """

    id = "dia_da_semana"
    title = "Dia da semana"

    def evaluate(self, ctx: AnalyticsContext) -> list[Insight]:
        window = ctx.weekday_window
        s = ctx.settings
        if window is None or window.end < window.start:
            return []
        if ctx.first_date is None or ctx.first_date > window.start:
            return _insufficient(
                self,
                f"Comparar dias da semana exige {s.weekday_weeks} semanas de histórico.",
            )
        by_weekday: list[list[int]] = [[] for _ in range(7)]
        day = window.start
        while day <= window.end:
            by_weekday[day.weekday()].append(ctx.daily_expenses.get(day, 0))
            day += datetime.timedelta(days=1)
        typical = [round(statistics.median(values)) if values else 0 for values in by_weekday]
        top = max(range(7), key=lambda i: typical[i])
        others = round(
            statistics.median(v for i, values in enumerate(by_weekday) if i != top for v in values)
        )
        if typical[top] < others * s.weekday_ratio or typical[top] - others < s.weekday_min_cents:
            return []
        evidence = [Evidence(f"Dia típico: {WEEKDAYS[i][0]}", typical[i]) for i in range(7)]
        evidence.append(Evidence("Dia típico: outros dias", others))
        comparison = (
            f", contra {_brl(others)} nos outros dias"
            if others
            else ", enquanto na maioria dos outros dias não há gastos"
        )
        message = (
            f"Você costuma gastar mais {WEEKDAYS[top][1]}: {_brl(typical[top])} num dia típico "
            f"(mediana das últimas {s.weekday_weeks} semanas){comparison}."
        )
        return [
            Insight(self.id, self.title, "info", message, tuple(evidence), _expense_filter(window))
        ]


_METHOD_TEXT = {
    "history": "Seu padrão de gasto até este dia nos meses anteriores",
    "linear": "Proporcional aos dias do mês (ainda sem histórico)",
    "closed": "Mês fechado",
}


class BudgetRule:
    """Orçamento estourado ou no ritmo de estourar (com o esperado e a projeção)."""

    id = "orcamento"
    title = "Orçamento"

    def evaluate(self, ctx: AnalyticsContext) -> list[Insight]:
        if not ctx.single_month or not ctx.budgets:
            return []
        period = _period_phrase(ctx)
        found = []
        for b in ctx.budgets:
            if b.status == "ok":
                continue
            subject = "Orçamento do mês" if b.category_id is None else f"Orçamento de {b.label}"
            if b.status == "over":
                message = (
                    f"{subject} estourado: {_brl(b.spent_cents)} de {_brl(b.limit_cents)} "
                    f"{period} ({format_pct(percent(b.spent_cents, b.limit_cents))})."
                )
            else:
                message = (
                    f"{subject} no ritmo de passar do limite: {_brl(b.spent_cents)} {period}, "
                    f"contra {_brl(b.expected_cents)} esperados até agora; projeção de "
                    f"{_brl(b.projected_cents or 0)} para um limite de {_brl(b.limit_cents)}."
                )
            evidence = [
                Evidence("Limite mensal", b.limit_cents),
                Evidence(f"Gasto {period}", b.spent_cents),
                Evidence("Esperado até agora", b.expected_cents),
                Evidence("Projeção para o mês", b.projected_cents),
                Evidence("Como o esperado é calculado", _METHOD_TEXT[b.method], "text"),
            ]
            ids = [b.category_id] if b.category_id else []
            found.append(
                Insight(
                    self.id,
                    self.title,
                    "attention",
                    message,
                    tuple(evidence),
                    _expense_filter(ctx.current, ids),
                )
            )
        if not found and not ctx.partial:
            count = len(ctx.budgets)
            subject = "O orçamento ficou" if count == 1 else f"Todos os {count} orçamentos ficaram"
            found.append(
                Insight(
                    self.id,
                    self.title,
                    "positive",
                    f"{subject} dentro do limite {period}.",
                    tuple(
                        Evidence(
                            f"{b.label}: gasto / limite",
                            f"{_brl(b.spent_cents)} / {_brl(b.limit_cents)}",
                            "text",
                        )
                        for b in ctx.budgets
                    ),
                    _expense_filter(ctx.current),
                )
            )
        return found[: ctx.settings.max_per_rule]


class PriceRule:
    """Lista de desejos: preço no alvo ou o menor da janela recente — hora boa de comprar."""

    id = "preco"
    title = "Lista de desejos"

    def evaluate(self, ctx: AnalyticsContext) -> list[Insight]:
        found = []
        for p in ctx.prices:
            evidence = [
                Evidence(f"Melhor preço agora ({p.store})", p.current_cents),
                Evidence("Seu preço-alvo", p.target_cents),
                Evidence(f"Menor antes, últimos {p.window_days} dias", p.previous_low_cents),
            ]
            if p.kind == "target":
                message = (
                    f"{p.item}: {_brl(p.current_cents)} na {p.store}, dentro do seu alvo de "
                    f"{_brl(p.target_cents or 0)}."
                )
            else:
                message = (
                    f"{p.item}: {_brl(p.current_cents)} na {p.store}, o menor preço dos últimos "
                    f"{p.window_days} dias (antes: {_brl(p.previous_low_cents or 0)})."
                )
            found.append(
                Insight(self.id, self.title, "positive", message, tuple(evidence), url=p.url)
            )
        return found[: ctx.settings.max_per_rule]


DEFAULT_RULES: tuple[InsightRule, ...] = (
    MonthSummaryRule(),
    BudgetRule(),
    PriceRule(),
    AboveAverageRule(),
    TrendRule(),
    ConcentrationRule(),
    WeekdayRule(),
)
_SEVERITY_ORDER = {"attention": 0, "positive": 1, "info": 2}


def evaluate(ctx: AnalyticsContext, rules: Sequence[InsightRule] = DEFAULT_RULES) -> list[Insight]:
    """Todos os insights: o resumo primeiro, depois alertas, pontos positivos e informativos;
    os de "dados insuficientes" no fim."""
    insights = [insight for rule in rules for insight in rule.evaluate(ctx)]
    return sorted(insights, key=_display_order)


def _display_order(insight: Insight) -> tuple[bool, bool, int]:
    return (
        insight.insufficient,
        insight.rule_id != MonthSummaryRule.id,
        _SEVERITY_ORDER[insight.severity],
    )


def weekday_window(current: DateRange, today: datetime.date, weeks: int) -> DateRange:
    """Últimas `weeks` semanas completas de dias já encerrados até o fim do recorte."""
    end = min(current.end, today - datetime.timedelta(days=1))
    return DateRange(end - datetime.timedelta(days=weeks * 7 - 1), end)
