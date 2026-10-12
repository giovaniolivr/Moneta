# Moneta — Proposta técnica (v0, em discussão)

> Status: **validada** (2026-10-11). Decisões registradas na §12. Itens **[ADIAR]** ficam para depois.

---

## 1. Análise crítica da proposta

O escopo do MVP está bem delimitado. Os pontos abaixo são problemas reais que, se não forem
tratados agora, geram retrabalho ou números errados mais tarde.

### 1.1 Arquitetura

| # | Problema | Recomendação |
|---|----------|--------------|
| A1 | O prompt sugere *local-first no navegador* (seção 3.2), mas a stack é Django. As duas coisas puxam para lados opostos: local-first exige lógica de negócio em JS e sincronização futura (o problema mais difícil do roadmap). | **Servidor como fonte da verdade** desde o dia 1: Django + SQLite. Backup = cópia do arquivo + exportação JSON. Local-first fica descartado. |
| A2 | "Expansão para web/mobile" exige uma API. Construir SPA + API agora dobra o trabalho para um único usuário. | MVP com **templates Django + HTMX** (renderizado no servidor). Toda regra de negócio em uma **camada de serviços** independente das views. A API (`/api/v1`, django-ninja) vira apenas outro "cliente" dessa camada quando o app mobile existir. PWA cobre o celular até lá. |
| A3 | Multiusuário "depois" costuma significar reescrever todas as queries. | Preparar **agora** (sem tela de cadastro): `User` customizado, `owner` em todo dado, queries sempre escopadas por usuário, login obrigatório. Ver §5. |
| A4 | Risco de anemia ou de over-engineering (repositórios, DDD completo, microsserviços). | Django "com disciplina": `models` + `services` (escrita) + `selectors` (leitura) + `domain` (cálculo puro, sem Django). Sem repositórios abstratos — o ORM já é a infraestrutura. |

### 1.2 Modelagem financeira (os riscos mais caros)

| # | Problema | Recomendação |
|---|----------|--------------|
| F1 | **Cartão de crédito gera dupla contagem**: a compra é despesa, e o pagamento da fatura *também* parece despesa. | Cartão é uma **Conta** (`kind=credit_card`). Compras = despesas nessa conta. Pagar a fatura = **transferência** conta corrente → cartão. Despesa contada uma vez só, na data da compra. |
| F2 | "Forma de pagamento" e "conta" se sobrepõem (crédito é as duas coisas). | `payment_method` é um enum simples (pix, débito, crédito, dinheiro, boleto, outro) para análise; `account` é entidade com saldo. São dimensões diferentes. |
| F3 | Comparar o **mês corrente parcial** com meses fechados produz alertas falsos ("gastos caíram 60%!" no dia 5). | O motor de análises compara mês corrente **pro-rata** (até o mesmo dia) ou só meses fechados, e declara qual. |
| F4 | Valor com sinal (+/-) mais tipo da movimentação = duas fontes de verdade. | `amount_cents` sempre **positivo** (`BigIntegerField`, `CHECK > 0`); o sinal deriva do `kind`. |
| F5 | `DateTimeField` para a data da movimentação causa bugs de fuso (compra às 23h vira o dia seguinte em UTC). | `date` é `DateField` (data civil, America/Sao_Paulo). Timestamps de auditoria (`created_at`) são `DateTimeField` com TZ. |
| F6 | Parcelamento e recorrência futuros. | Não implementar agora; deixar espaço: movimentações terão FK opcionais futuras (`installment_plan`, `recurrence`) e o conceito de *status* (`posted` vs `planned`) entra na Fase 2 para separar histórico de projeção. Nenhuma migração destrutiva necessária. |
| F7 | "Saldo disponível" sem saldo inicial é chute. | Cada conta tem `opening_balance_cents` + `opening_date`. Sem isso o dashboard mostra "resultado do período", não "saldo". |

### 1.3 Produto / UX

- **Entrada rápida é o produto.** Se registrar demorar mais que ~10 s, o hábito morre e a
  dashboard fica vazia. Formulário com só 3 campos obrigatórios (valor, tipo, categoria),
  data padrão = hoje, conta padrão = a última usada, teclado numérico no celular, tags com
  autocomplete. Botão "+" fixo em todas as telas.
- **Dashboard "completa" vs "sem excesso"** são tensões. Proposta: 4 KPIs + 3 gráficos na
  primeira dobra; o resto em abas/seções expansíveis. Ver §7.
- **Categorias predefinidas**: semear uma lista padrão por usuário (copiada, não compartilhada),
  editável. Separar categorias de despesa e de receita.

### 1.4 Sobre o `DESIGN.md`

O `DESIGN.md` é **inspiração, não especificação** — referência de estilo principalmente para o
modo escuro, tipografia e tamanhos. Cores, fontes e escalas podem ser ajustadas livremente.
Adaptações previstas:

1. **Escala de marketing ≠ escala de app.** `display-xl` 72px, seções de 96px e `stat-display`
   56px são grandes demais para uma dashboard densa. Criar uma escala de app (KPI ~28–32px,
   seções 24–32px).
2. **"Não introduza segunda cor"** colide com gráficos: distribuição por categoria precisa de
   uma paleta categórica (6–8 cores distinguíveis). O amarelo continua sendo a cor da marca/CTA;
   os gráficos ganham paleta própria documentada.
3. **Receita/despesa em verde/vermelho**: ok, mas sempre com sinal ou ícone junto (daltonismo).
4. **Temas escuro (padrão) e claro**: tokens semânticos em variáveis CSS (`--color-ink`,
   `--color-surface`, `--color-primary`…) com valores por tema. Amarelo `#faff69` sobre branco não
   tem contraste, então o tema claro usa um "primary" mais escuro para texto/bordas e mantém o
   amarelo só como fundo de CTA. Regra: **todo par texto/fundo dos tokens deve passar WCAG AA
   (4.5:1) nos dois temas**, verificado por teste automatizado sobre a tabela de tokens.
5. Renomear de "ClickHouse-design-analysis" para Moneta e remover componentes que não se aplicam
   (pricing, logo strip, events, code-window).

---

## 2. Stack recomendada

| Camada | Escolha | Por quê | Alternativa descartada |
|--------|---------|---------|------------------------|
| Linguagem | **Python 3.14** via `uv` | `uv` já instalado; gerencia Python, venv e lock. `uuid.uuid7()` nativo. | pip/poetry |
| Framework | **Django 6.1** → **6.2 LTS** quando sair (abr/2027) | CSP nativo, template partials nativos (ideal p/ HTMX), framework de tasks, `LoginRequiredMiddleware`. Ver ADR 0001. | Django 5.2 LTS (mais suporte, menos recursos) · FastAPI |
| Banco | **SQLite (WAL)** → PostgreSQL quando houver multiusuário real | Custo zero, backup = um arquivo. ORM torna a troca barata. | Postgres já no MVP (infra sem benefício agora) |
| Interatividade | **HTMX** + **Alpine.js** (build CSP, pontual) + template partials nativos | Sem build de JS, sem SPA, sem estado duplicado. | React/Vue SPA |
| Estilo | **CSS próprio com tokens** (variáveis por tema, `@layer`), sem build — ADR 0002 | Zero dependências, CSP estrita, contraste testado. Inter e ícones Lucide servidos localmente. | Tailwind v4 (exige binário e build) |
| Gráficos | **SVG próprio** (`static/js/charts.js`) — ADR 0003 | Especificações de dataviz, paleta validada para daltonismo, CSP estrita, tema instantâneo. | Apache ECharts, Chart.js |
| API futura | **django-ninja** | Tipada (Pydantic), OpenAPI automático, chama os mesmos services. | DRF (mais verboso) |
| Qualidade | `ruff` (lint+format), `mypy` + `django-stubs`, `pytest` + `pytest-django`, `hypothesis` só para o núcleo monetário | Tipagem e testes do domínio financeiro. | — |
| Config | `django-environ` (`.env`) | Segredos fora do código. | — |

**Dependências de runtime no MVP:** Django, django-environ, django-stubs-ext, argon2-cffi. Só isso. HTMX/Alpine/ECharts como arquivos estáticos vendorizados.

**Execução:** 100% local com `manage.py runserver`. Deploy para outros usuários **[ADIAR]**;
quando vier: Postgres, servidor WSGI/ASGI, `settings/prod.py` (já existe e é testado). Acesso pelo
celular antes disso, se desejado: **Tailscale** (gratuito, privado). Alternativas gratuitas com disco
persistente: VM Always Free da Oracle Cloud ou PythonAnywhere. ⚠️ Evitar hosts de disco
efêmero (ex.: Render free) com SQLite — os dados somem a cada deploy.

---

## 3. Arquitetura

### 3.1 Camadas

```
 Views (HTML/HTMX)      API v1 (futuro)      Assistente LLM (futuro)
         \                    |                     /
          └──────────►  services / selectors  ◄────┘     ← única porta de entrada
                              |
                ┌─────────────┴──────────────┐
           models (ORM)                 domain (Python puro)
           persistência                 Money, cálculos, regras de análise
```

Regras:
- **Views não contêm regra de negócio.** Validam forma (forms), chamam services/selectors, renderizam.
- **`services.py`**: operações de escrita (`create_transaction(user, data)`, `merge_tags(user, src, dst)`).
  Transacionais, validam regras de negócio, recebem sempre `user`.
- **`selectors.py`**: leituras e agregações via ORM (somas por mês/categoria rodam no SQL), sempre
  filtradas por `owner`.
- **`domain/`**: funções e dataclasses puras, sem import de Django — testáveis em milissegundos e
  reaproveitáveis por API, LLM e simulador.

### 3.2 Estrutura de pastas

```
moneta/
├── pyproject.toml / uv.lock
├── manage.py
├── .env.example
├── config/                    # projeto Django
│   ├── settings/{base,dev,prod,test}.py
│   ├── urls.py
│   └── wsgi.py / asgi.py
├── apps/
│   ├── core/                  # infraestrutura compartilhada
│   │   ├── models.py          # TimeStampedModel, OwnedModel, OwnedQuerySet
│   │   ├── money.py           # parse/format BRL ↔ centavos
│   │   ├── names.py           # normalização de nomes (contas, categorias, tags)
│   │   └── templatetags/      # |brl, |data_br
│   ├── users/                 # User customizado (sem cadastro público)
│   ├── ledger/                # contas e movimentações
│   │   ├── models.py          # Account, Transaction
│   │   ├── services.py
│   │   ├── selectors.py       # filtros e consultas
│   │   ├── filters.py         # TransactionFilter (dataclass) ← compartilhado com dashboard
│   │   ├── forms.py / views.py / urls.py / templates/ledger/
│   │   └── tests/
│   ├── taxonomy/              # categorias, tags, aliases
│   │   ├── defaults.py        # categorias padrão de todo usuário novo
│   │   └── services.py        # create_or_get_tag, rename, merge
│   ├── analytics/             # SEM models
│   │   ├── selectors.py       # agregações SQL → dataclasses
│   │   ├── domain/
│   │   │   ├── metrics.py     # KPIs, médias, variações
│   │   │   └── insights/      # uma regra por arquivo + registry
│   │   └── tests/
│   ├── dashboard/             # views e templates da dashboard
│   └── portability/           # exportação/importação JSON e CSV, backup
├── templates/                 # base.html, componentes (cotton/partials)
├── static/                    # css (tailwind), js vendor (htmx, alpine, echarts)
└── docs/
    ├── ARQUITETURA.md
    └── adr/                   # 1 arquivo por decisão relevante
```

Futuro (não criar agora): `apps/goals`, `apps/planning` (recorrências, parcelamentos, orçamentos),
`apps/investments` (simulador), `apps/shopping` (preços), `apps/assistant` (LLM), `apps/api`.

---

## 4. Modelagem inicial

Todos os modelos de dados do usuário herdam de `OwnedModel`:

```python
class OwnedModel(TimeStampedModel):
    id = UUIDField(primary_key=True, default=uuid7)   # opaco nas URLs, estável em export/import
    owner = ForeignKey(settings.AUTH_USER_MODEL, on_delete=CASCADE, db_index=True)
    objects = OwnedQuerySet.as_manager()              # .for_user(user)
    class Meta: abstract = True
```

UUID como PK: URLs não enumeráveis, import/export e sincronização futura sem colisão de IDs.

### Account
| campo | tipo | nota |
|---|---|---|
| name | char | "Nubank", "Carteira" |
| kind | enum | `checking`, `savings`, `cash`, `credit_card`, `investment` |
| currency | char(3) | `BRL` fixo por ora; evita migração se um dia houver outra moeda |
| opening_balance_cents | bigint | saldo na data de abertura |
| opening_date | date | |
| is_archived | bool | nunca apagar conta com histórico |

O MVP cria uma conta padrão ("Principal"). Múltiplas contas funcionam desde o início, mas a UI
pode escondê-las até você precisar.

### Category
| campo | tipo | nota |
|---|---|---|
| name | char | nome de exibição |
| kind | enum | `expense` / `income` |
| color | enum `1`–`8` | **posição na paleta** do tema, não hex: cada tema define a cor com contraste garantido |
| icon | char | nome do ícone (Lucide) |
| is_archived | bool | |
| unique | (owner, kind, normalized_name) | |

Subcategorias **[ADIAR]**: um `parent` nulo entra numa migração simples quando houver necessidade.
Categorias padrão (`taxonomy/defaults.py`) **não incluem "Investimentos"**: aplicar dinheiro é
transferência para uma conta `investment`, não consumo.

Contas e categorias padrão são criadas por *signals* de criação de usuário (um por app, cada app
cuida do próprio bootstrap) e, para usuários pré-existentes, por migração de dados.

### Tag / TagAlias
```
Tag(owner, name, normalized_name)          unique(owner, normalized_name)
TagAlias(owner, normalized_alias, tag FK)  unique(owner, normalized_alias)
```

**Normalização determinística** (`core/names.py`, sem LLM; a mesma usada para nomes de contas e categorias):
`NFKC → strip → colapsa espaços → casefold → remove acentos (NFKD sem marcas combinantes)`.
Ex.: `"  MERCADO "`, `"Mercado"`, `"mercado"` → `mercado`; `"Saúde"` → `saude`.

- Criar tag: normaliza → procura em `Tag.normalized_name` → procura em `TagAlias` → senão cria.
  O `name` de exibição é o da primeira criação (editável).
- **Mesclar** "Supermercado" em "Mercado" (ação explícita do usuário): move as associações
  (de forma genérica, para qualquer modelo com M2M para `Tag`), transfere os apelidos da origem,
  cria `TagAlias("supermercado" → Mercado)` e apaga a tag de origem. Digitar "supermercado"
  depois cai automaticamente em "Mercado". Nada é mesclado por similaridade semântica.
- **Apelido manual**: permitido se o texto não for nome de outra tag (nesse caso, o caminho é
  mesclar) nem apelido de outra tag.
- **Renomear** não cria apelido para o nome antigo (equivalências nunca são implícitas); se o
  novo nome era apelido da própria tag, o apelido é absorvido.
- **Autocomplete** (`search_tags`): busca por nome e apelido em qualquer posição, prefixos primeiro.
- A base `core.models.NormalizedNameModel` concentra nome de exibição + chave normalizada para
  contas, categorias e tags.
- Acentos são removidos na forma normalizada (decidido); o nome de exibição mantém os acentos.

### Transaction
| campo | tipo | nota |
|---|---|---|
| kind | enum | `income`, `expense`, `transfer` |
| amount_cents | bigint, CHECK > 0 | sinal vem do `kind` |
| date | date | data civil |
| account | FK Account | origem (despesa/transferência) ou destino (receita) |
| destination_account | FK Account, null | obrigatório só em `transfer`; CHECK no banco |
| category | FK Category, null | obrigatório em income/expense; proibido em transfer; `kind` deve bater |
| tags | M2M Tag | |
| payment_method | enum, null | |
| description | char, opcional | |
| notes | text, opcional | |

Índices: `(owner, date)`, `(owner, category, date)`, `(owner, kind, date)`.

Regras-chave (testadas):
- Transferências **nunca** entram em receitas/despesas consolidadas.
- Saldo da conta = abertura + receitas − despesas − transferências saindo + transferências entrando.
  O saldo de abertura vale no **início** de `opening_date`; movimentações anteriores a essa data
  ficam como histórico para análises e não alteram o saldo (permite lançar meses passados).
- Resultado do período = receitas − despesas (ignora transferências).
- Forma das transferências garantida **no banco** (CHECK): transferência ⇔ conta de destino e sem
  categoria; destino ≠ origem; valor entre 1 centavo e `MAX_CENTS`.
- Regras com objetos relacionados em `Transaction.clean()` (vale para services e admin): mesmo
  dono em contas/categoria, tipo da categoria = tipo da movimentação, data não futura (projeções
  são da fase 2).
- Contas/categorias arquivadas não recebem movimentações novas; as antigas seguem editáveis.
- Conta e categoria com histórico são protegidas contra exclusão (`RESTRICT`): arquiva-se.
  `RESTRICT` (e não `PROTECT`) permite excluir o **usuário** com todos os dados de uma vez.
- Entrada única `TransactionData` (dataclass) para criar/editar — reaproveitada por formulários,
  importação, API e assistente.

### Dinheiro no código
- Banco e domínio: **inteiros em centavos**. Nunca `float`.
- Entrada: `parse_brl("1.234,56") → 123456` (aceita `45`, `45,9`, `R$ 45,90`).
- Saída: filtro `|brl` → `R$ 1.234,56`.
- Divisões (médias, percentuais) usam `Decimal` com arredondamento explícito
  (`ROUND_HALF_EVEN`) só na apresentação.

---

## 5. Segurança e preparação para multiusuário (sem fluxo de cadastro)

Feito **agora**, custa pouco; feito depois, custa uma reescrita:

1. **`AUTH_USER_MODEL = "users.User"`** antes da primeira migração (impossível trocar depois sem dor).
2. **Login obrigatório** em tudo (`LoginRequiredMiddleware`, nativo). Sem tela de cadastro;
   seu usuário é criado com `createsuperuser`. Há apenas tela de login/logout.
3. **Isolamento por `owner`**: toda leitura passa por `Model.objects.for_user(user)`; todo service
   recebe `user`; lookups por ID sempre com `owner=user` (retorna 404, não 403, para não vazar
   existência). Teste automatizado: "usuário B não vê nem altera nada do usuário A" em cada
   endpoint.
4. **Validação de FKs cruzadas**: ao salvar uma transação, conta/categoria/tags devem ser do mesmo
   `owner` (validado no service, não só no form).
5. **Config segura**: `SECRET_KEY` e `DEBUG` via `.env`; hasher Argon2; **CSP nativo** (só
   `'self'`, sem inline); em prod: `SECURE_*`,
   cookies `Secure/HttpOnly/SameSite`, HSTS, `ALLOWED_HOSTS` restrito; CSRF (HTMX envia o token
   via header configurado no `base.html`).
6. **Rate limit no login** [ADIAR até expor à internet] (`django-axes`).
6b. **Login automático em desenvolvimento** (`apps/core/dev_autologin.py`): enquanto o app é de
   um único usuário local, `runserver` entra direto. Só funciona com `DEBUG=True` + requisição de
   127.0.0.1/::1 + middleware presente (adicionado apenas em `settings/dev.py`); um system check
   recusa subir com o middleware e `DEBUG=False`. Desliga com `DEV_AUTO_LOGIN=0`.
7. **Dados sensíveis**: `.env`, `db.sqlite3` e backups no `.gitignore` desde o primeiro commit.

---

## 6. Motor de indicadores e análises

### 6.1 Fluxo
```
TransactionFilter (período, categorias, tags, tipo, forma pgto, contas)
      │
      ▼
analytics.selectors  ── agregações SQL (SUM/COUNT GROUP BY mês, categoria, tag…)
      │                  retorna dataclasses imutáveis (MonthlyTotals, CategoryTotals…)
      ▼
analytics.domain.metrics   ── KPIs puros (resultado, médias, variações, participação)
analytics.domain.insights  ── regras → list[Insight]
      │
      ▼
dashboard views (HTML) / API futura / LLM futura
```

O **mesmo `TransactionFilter`** (`ledger/filters.py`) alimenta a lista de movimentações e a
dashboard: clicar numa fatia do gráfico abre a lista com exatamente aqueles filtros (drill-down
consistente por construção; `TransactionFilter.to_query()` gera o link). Semântica: valores da
mesma dimensão combinam com OU, dimensões diferentes com E; conta casa com origem ou destino;
filtro por tag usa subquery (sem linhas duplicadas nas somas). Parâmetros de URL em português
(`periodo`, `de`, `ate`, `tipo`, `categoria`, `tag`, `conta`, `pagamento`, `q`, `pagina`).

### 6.2 Indicadores do MVP
Receitas, despesas, resultado, saldo (se houver saldo de abertura), média diária de gastos
(despesas ÷ dias decorridos do período), média mensal (meses fechados), variação vs período
anterior equivalente, participação por categoria/tag/forma de pagamento, taxa de poupança
(resultado ÷ receitas).

### 6.3 Regras de análise (insights)
Implementadas em `analytics/domain/insights.py` (Python puro). `dashboard.build_insights()` agrega
o histórico no banco e monta um `AnalyticsContext`; cada regra é uma classe pequena:

```python
class InsightRule(Protocol):
    id: str
    title: str
    def evaluate(self, ctx: AnalyticsContext) -> list[Insight]: ...   # [] = nada a dizer

@dataclass(frozen=True)
class Insight:
    rule_id: str
    title: str
    severity: Literal["info", "attention", "positive"]
    message: str                      # "Alimentação: R$ 500,00 em outubro até o dia 15, 66,7% acima…"
    evidence: tuple[Evidence, ...]    # (rótulo, valor, unidade brl|pct|text) → "Por quê?" e testes
    filter: TransactionFilter | None  # link para as movimentações que geraram o insight
    insufficient: bool = False        # "ainda sem dados suficientes" (declarado, não omitido)
```

Uma regra pode devolver vários insights (ex.: mais de uma categoria acima da média, limitado por
`max_per_rule`). Limiares ficam em `InsightSettings` (configuráveis no futuro por usuário).

| Regra | O que diz | Cuidados |
|---|---|---|
| Resumo | gasto do período, variação vs anterior equivalente, quanto sobrou | período em andamento no vermelho só informa (é comum antes do salário) |
| Acima da média | categoria do mês ≥ +15% **e** ≥ R$ 50 acima da média dos 3 meses anteriores | só períodos de um mês; mês em andamento usa os **mesmos dias** de cada mês da base; a partir do 7º dia |
| Tendência | total ou categoria subindo/caindo 3 meses seguidos (variação ≥ R$ 50) | só meses fechados; 4 meses completos |
| Concentração | menor grupo (até 3) de categorias com ≥ 50% das despesas | o grupo precisa ter ≥ 1,5× a fatia "justa" (k/n) — fatias iguais não são concentração |
| Dia da semana | dia da semana com gasto típico ≥ 1,5× os demais (e ≥ R$ 20) | **mediana** dos totais diários das últimas 8 semanas: um gasto isolado não vira padrão |

Histórico = só **meses completos** a partir do primeiro mês inteiro de uso (o mês da primeira
receita/despesa, se começou no meio, fica de fora — subestimaria a média). Sem histórico
suficiente, a regra devolve um insight `insufficient` dizendo quanto falta.

---

## 7. Telas e fluxos

1. **Login** — único fluxo de autenticação.
2. **Dashboard** (home)
   - Barra de período (Mês ▸ atual, Semana, Ano, Personalizado) + filtros em gaveta.
   - Linha de 4 KPIs: Receitas · Despesas · Resultado · Saldo (ou média diária).
   - Insights (até 3, com "ver mais").
   - Gráficos: receitas × despesas por mês (barras) · despesas por categoria (rosca/barras
     horizontais) · resultado mensal (linha).
   - Seções abaixo da dobra: comparativo entre meses (categorias × 6 meses, sparkline dos meses
     fechados, média e variação do último mês — pro-rata se em andamento), tags e formas de
     pagamento com variação vs período anterior equivalente.
   - Clique em qualquer elemento → lista filtrada.
3. **Movimentações** — lista agrupada por dia, filtros combináveis, busca, edição inline/modal,
   exclusão com "desfazer".
4. **Nova movimentação** — modal/bottom sheet acionado pelo "+" global. Valor em destaque com
   teclado numérico, alternador Despesa/Receita/Transferência, categorias como chips (as mais
   usadas primeiro), tags com autocomplete, "mais opções" recolhido. "Salvar e adicionar outra".
5. **Categorias e tags** — CRUD, renomear, arquivar, **mesclar tags**, ver aliases.
6. **Contas** — CRUD simples, saldo de abertura.
7. **Dados** — exportar JSON/CSV, importar JSON (com prévia), baixar backup.

Mobile: navegação inferior (Dashboard · Movimentações · + · Mais), KPIs em carrossel 2×2.

---

## 8. Backup, migração e evolução dos dados

- **Migrações Django** versionam o schema; nenhuma migração destrutiva sem passo de dados.
- **Export JSON próprio** (não `dumpdata`): `{"format": "moneta", "schema_version": 1, ...}` com
  UUIDs. Import idempotente (upsert por UUID) e com migradores entre versões de schema.
- **CSV** de movimentações para planilhas.
- **Backup do SQLite**: comando `manage.py backup` usando a API de backup online do SQLite
  (`sqlite3.Connection.backup`), rotação de N cópias. Agendável no Agendador de Tarefas do Windows.
- Migração SQLite → Postgres: export JSON → import, já testado pelo mesmo mecanismo.

Implementação (`apps/portability`):
- `schema.py`: envelope, versão e `MIGRATIONS[v]` (converte arquivos da versão `v` para `v + 1`).
- Importação (`services.import_payload`): upsert por UUID; sem o UUID, conta/categoria/tag com o
  mesmo nome normalizado é **reaproveitada** (arquivo de outra instalação não duplica "Alimentação").
  O arquivo vence nas diferenças; nada é apagado; tudo-ou-nada com **todos** os erros listados;
  UUID de outro usuário do servidor é recusado. A tela faz uma simulação (`dry_run`) como prévia
  e só grava na confirmação.
- CSV: `;`, vírgula decimal, BOM, datas dd/mm/aaaa; "Valor" sempre positivo (o efeito vem de
  "Tipo"); células que começam com `= + - @` recebem `'` (injeção de fórmula).
- O backup do arquivo SQLite é só por linha de comando: num servidor multiusuário o banco tem dados
  de todos — na web, cada usuário baixa apenas o próprio JSON.

## 9. Testes

| Nível | O quê | Ferramenta |
|---|---|---|
| Unitário puro | `money`, `normalization`, `metrics`, cada regra de insight | pytest (+ hypothesis em money/normalização) |
| Integração | services/selectors com banco: transferências fora do consolidado, saldo, merge de tags, isolamento por owner | pytest-django |
| Fluxo | criar → filtrar → ver na dashboard; export → import → mesmos totais | Django test client |
| Contrato de segurança | toda URL exige login; usuário B recebe 404 em objetos de A | teste parametrizado sobre todas as rotas |

Meta: 100% de cobertura em `domain/` e `money`; o resto, pelos fluxos essenciais.

---

## 10. Roadmap técnico da Fase 1 (entregas pequenas e independentes)

| # | Entrega | Pronto quando |
|---|---------|---------------|
| 0 ✅ | Esqueleto: uv, Django, settings divididos, ruff/mypy/pytest, `.gitignore`, `core`, `users`, login | `pytest` verde; login funciona; nenhuma página acessível sem login |
| 1 ✅ | `core.money` + filtros de template | testes de parse/format BRL |
| 2 ✅ | Contas e categorias (models, seed padrão, admin) | seed cria categorias para um usuário novo |
| 3 ✅ | Tags + normalização + aliases + merge | testes de normalização e merge |
| 4 ✅ | Transaction + services (CRUD, validações, transferências) | regras da §4 testadas; merge de tags move as associações das movimentações |
| 5 ✅ | Layout base + tokens (temas escuro/claro) + componentes | tela navegável no celular; contraste AA testado |
| 6 ✅ | Formulário rápido de nova movimentação (painel lateral / inferior) | registro em < 10 s no celular |
| 6b ✅ | Lançamento em lote (`/movimentacoes/lote/`) | várias linhas, Enter cria a próxima, totais ao vivo, rascunho local, grava tudo-ou-nada |
| 7 ✅ | Lista de movimentações + `TransactionFilter` | filtros combináveis, edição, exclusão |
| 7b ✅ | Telas de categorias, tags (apelidos, mesclar) e contas | gestão completa sem usar o admin |
| 8 ✅ | `analytics` selectors + metrics | KPIs batem com cálculo manual em fixtures |
| 9 ✅ | Dashboard: KPIs + gráficos + drill-down | clique leva à lista filtrada |
| 10 ✅ | Insights (regras + "dados insuficientes") | cada regra com teste |
| 11 ✅ | Seções secundárias da dashboard (tags, formas de pgto, comparativos) | comparativo por categoria com média pro-rata; variação vs período anterior |
| 12 ✅ | Export/import JSON, CSV, comando de backup | round-trip preserva totais |
| 13 ✅ | Polimento: estados vazios, acessibilidade, desfazer exclusão, navegação ◂ ▸ entre meses (PWA adiado para a etapa de uso real) | usável diariamente |

### Roadmap da Fase 2 — planejamento

| # | Entrega | Pronto quando |
|---|---------|---------------|
| 14 ✅ | Movimentações **previstas × realizadas** (`status`), lista "Previstas", "A confirmar" | análises/saldos só com realizadas; data futura só em prevista |
| 15 ✅ | Recorrências (`apps/planning`): mensal/semanal/anual, gera previstas num horizonte | confirmar/ajustar/pular; idempotente |
| 16 ✅ | Parcelamentos no cartão: compra em N× gera N parcelas previstas | cada parcela conta no seu mês |
| 17 ✅ | Orçamentos mensais por categoria + ritmo pro-rata + insight | alerta só com evidência |
| 18 | Projeção de saldo por conta e fatura prevista do cartão | projeção usa só previstas confirmáveis |
| 19 | Metas (`apps/goals`) sobre o saldo de uma conta | aporte mensal necessário vs. capacidade de poupança |
| 20 | Dashboard realizado × previsto + polimento | |

## 11. Preparação para os módulos futuros

- **Objetivos (Fase 2)**: novo app `goals`; lê capacidade de poupança via `analytics.selectors`
  (média de resultado mensal). Nenhuma mudança no `ledger`.
- **Recorrências/parcelamentos (Fase 2)**: app `planning` gera transações `status=planned`;
  `analytics` passa a separar realizado × projetado pelo `status`.
- **Rendimento de contas (Fase 3)**: a conta poderá ter um *produto* (ex.: Caixinha Turbo ou
  normal do Nubank, CDB a 100% do CDI) para estimar quanto o saldo rende com o tempo — base para
  metas mais precisas e para o simulador. Configuração do produto separada das taxas de mercado.
- **Simulador (Fase 3)**: núcleo em Python puro (`investments/domain`): fluxos de caixa datados,
  taxa diária, IR regressivo, IOF, comparação de estratégias por **valor presente / saldo final na
  mesma data-base**. Produtos (Caixinha Nubank) como configuração separada das taxas de mercado
  (CDI/Selic via API SGS do Banco Central, com cache).
- **Pesquisa de preços (Fase 4)**: app `shopping`, coletores isolados atrás de uma interface
  (`PriceSource`), executados em tarefas de fundo; só fontes permitidas.
- **LLM (Fase 5)**: app `assistant` expõe os **services existentes como ferramentas**. A LLM propõe
  uma ação → o service valida → o usuário confirma. Cálculo nunca é feito pela LLM. Provedor
  atrás de uma interface para ser trocável.
- **Mobile nativo / sincronização**: API django-ninja sobre os mesmos services; UUIDs e
  `updated_at` já viabilizam sincronização incremental.

## 12. Decisões

**Tomadas (2026-10-11)**
1. Servidor como fonte da verdade (sem local-first no navegador).
2. Django 6.1 (→ 6.2 LTS) + SQLite + HTMX, sem SPA — ADR 0001.
3. User customizado + `owner` em tudo + login obrigatório, sem cadastro público.
4. Centavos inteiros; `amount` positivo + `kind`.
5. Cartão de crédito como conta; fatura como transferência.
6. Normalização de tags remove acentos e caixa.
7. Temas escuro (padrão) e claro, com contraste AA garantido por teste.
8. Execução local (`runserver`); deploy multiusuário é fase futura.
9. `DESIGN.md` é inspiração visual, não especificação.

**Tomadas (2026-10-12)**
10. Análises por **mês civil da data da movimentação** (competência), não pelo ciclo da fatura:
    uma compra de 28/09 no cartão conta em setembro, mesmo caindo na fatura de outubro; o
    pagamento da fatura é transferência e não conta.

**Tomadas para a Fase 2 (2026-10-12)**
11. Parcelamento: **cada parcela conta no mês da própria parcela** (R$ 1.200 em 12× = R$ 100/mês);
    a compra fica agrupada num plano de parcelamento. Centavos que sobram vão na 1ª parcela.
    Parcelas com data até hoje nascem realizadas (a compra aconteceu); as futuras, previstas.
12. Recorrências e parcelas viram **previstas**; só entram nas análises quando o usuário
    **confirma** (pode ajustar valor/data ou pular). Nada é lançado sozinho.
14. Orçamento: o "esperado até hoje" segue a **fração que o próprio usuário costuma gastar até
    aquele dia** nos meses completos anteriores (até 3), não uma reta — gastos fixos do começo do
    mês não geram alarme falso. Sem 2 meses de histórico, usa a reta e declara isso. Projeção só
    a partir do 7º dia; "ritmo acima" exige excesso projetado ≥ R$ 20.
13. Metas medem o progresso pelo **saldo de uma conta** (ex.: Caixinha); aportes são as
    transferências que o usuário já faz.

**Adiadas**
Deploy/hospedagem · Postgres · API · subcategorias · orçamentos · rate limit · tarefas em fundo ·
i18n (o app nasce pt-BR, mas com strings marcadas para tradução).
