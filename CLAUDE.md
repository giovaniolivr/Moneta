# Moneta

Plataforma de gestão e planejamento financeiro pessoal. Uso próprio no início, arquitetada para
virar multiusuário e ganhar clientes web/mobile. Idioma do produto e da documentação: pt-BR.
Código (identificadores) em inglês.

**Status:** Fase 1 (entregas 0–13) concluída; PWA/mobile adiado para a etapa de uso real. Seguir o roadmap da §10 de
`docs/ARQUITETURA.md`, uma entrega por vez.

## Pendência: configurar o uso real (fazer junto com o usuário, quando ele pedir)
O usuário ainda não configurou o app para dados reais. Quando ele quiser começar a usar, guiar
passo a passo (sem ler nem exibir o conteúdo de `.env`; ele cola os valores):
1. `.env` a partir de `.env.example`; gerar `DJANGO_SECRET_KEY` nova (comando no arquivo).
2. `DATABASE_URL=sqlite:///db.sqlite3` → `migrate` → `createsuperuser` (seu usuário/senha).
3. Decidir `DEV_AUTO_LOGIN` (com dados reais, provavelmente `0`) e `DEV_AUTO_LOGIN_USERNAME`.
4. Agendar `manage.py backup` no Agendador de Tarefas do Windows (diário) e testar um restore.
5. Fazer um export JSON inicial e conferir que `db.sqlite3`/`.env`/`backups/` seguem fora do git.
Mobile/PWA também fica para essa etapa ("usar o app em si").

## Documentos de referência
- `docs/ARQUITETURA.md` — stack, camadas, modelagem, roadmap, decisões. Fonte da verdade técnica.
- `docs/adr/` — registro de decisões (um arquivo por decisão relevante).
- `DESIGN.md` — **inspiração** visual (modo escuro, tipografia, tamanhos), não especificação.
  Pode ser ajustado. No código, usar sempre tokens (variáveis CSS), nunca hex soltos. Temas
  escuro (padrão) e claro; todo par texto/fundo deve passar contraste WCAG AA nos dois.

## Stack
Python 3.14 (uv) · Django 6.1 → 6.2 LTS (ADR 0001) · SQLite (Postgres no futuro) · HTMX ·
CSS próprio sem build (ADR 0002) · gráficos SVG próprios (ADR 0003) · pytest · ruff · mypy.

## Regras inegociáveis
- **Dinheiro em centavos inteiros** (`amount_cents`, `BigIntegerField`). Nunca `float`.
  `amount_cents > 0`; o sinal vem de `kind` (income/expense/transfer).
- **Previstas nunca contam**: `Transaction.status` é `posted` (realizada) ou `planned` (prevista).
  Saldos e análises usam só realizadas — `TransactionFilter.status` já vem `"posted"`; qualquer
  consulta nova direto em `Transaction.objects` precisa filtrar o status. Data futura só em prevista.
  Previstas viram realizadas apenas por confirmação do usuário (`confirm_transaction`).
- **Transferências nunca contam** como receita ou despesa nas análises consolidadas.
  Pagamento de fatura de cartão = transferência.
- Datas de movimentação são `DateField` (data civil, America/Sao_Paulo).
- **Todo dado do usuário herda de `OwnedModel`** (UUID PK + `owner`). Toda leitura via
  `.for_user(user)`; todo service recebe `user`; lookup por id sempre com `owner=user` (404).
  FKs relacionadas devem pertencer ao mesmo `owner`.
- Login obrigatório em todas as rotas. Não existe fluxo de cadastro público.
- Regras de negócio fora das views: escrita em `services.py`, leitura em `selectors.py`,
  cálculo puro (sem import de Django) em `domain/`.
- Normalização de tags é determinística, sem LLM. Mesclagem só por ação explícita do usuário.
- Análises automáticas devem expor a evidência numérica e declarar quando não há dados
  suficientes. Não comparar mês parcial com meses fechados sem pro-rata.
- Não implementar funcionalidades de fases futuras antecipadamente.

## Interface
- Estilos só em `static/css/app.css`, usando tokens `var(--color-…)`; cores novas entram nos
  dois temas e passam pelo teste de contraste. Categorias usam `slot-1..8`, não hex.
- CSP estrita: proibido `style=""`, `onclick=` e `<script>` inline sem `nonce="{{ csp_nonce }}"`
  (verificado por teste). JS em `static/js/`.
- Ícones: `{% load ui %}{% icon "nome" %}`; para incluir um novo, adicionar em
  `scripts/build_icon_sprite.py` e rodar o script.
- Páginas de app estendem `layouts/app.html`; itens de menu em `apps/core/navigation.py`
  (ativam sozinhos quando a rota existe).
- Valores monetários sempre com sinal/ícone além da cor (`|tx_amount`, `|brl_signed`).
- Painel lateral: links com `data-drawer` abrem a URL no painel (`static/js/drawer.js`).
  Views usam `apps/core/drawer.py`: `render_drawer()` (fragmento ou página inteira via
  `layouts/drawer_page.html`) e `done()` (204 fecha o painel / redirect sem JS). Contrato no
  docstring do módulo. Erros de service → `apply_service_errors()` (`apps/core/forms.py`).
- Formulários com `instance=` usam `has_data(args, kwargs)` para decidir se aplicam os valores
  iniciais (`Form(request.POST or None)` passa `None` posicional).
- Regras de exclusão: conta/categoria com movimentações só arquiva; sempre ≥ 1 conta ativa.
- Dashboard: `apps/analytics` (domínio puro em `domain/`, agregações SQL em `selectors.py`,
  montagem em `dashboard.py`) → `apps/dashboard` (view/template). Período em andamento é sempre
  comparado pro-rata (`comparable_ranges`); período fechado com o anterior inteiro. Médias mensais
  só com meses fechados. Todo gráfico tem tabela equivalente e link de drill-down
  (`TransactionFilter.to_query()`).
- Análises automáticas: regras em `analytics/domain/insights.py` (classe com `id`, `title`,
  `evaluate(ctx) -> list[Insight]`), limiares em `InsightSettings`, contexto montado em
  `dashboard.build_insights()`. Todo insight tem `evidence` (tabela "Por quê?") e `filter`
  (drill-down); falta de histórico → `insufficient=True` com o que falta. Histórico = só meses
  completos. Nova regra entra em `DEFAULT_RULES` com testes de disparo, não disparo e insuficiente.
- Tabelas largas: `.table-scroll` (rola dentro do card; é `position: relative` para conter os
  `.sr-only`). Sparklines são SVG do servidor (`|spark_points`, eixo a partir de zero).
- Gráficos: dados via `json_script`, desenho em `static/js/charts.js`, cores pelas classes
  `viz-*` (tokens `--color-chart-1..8`, paleta validada pelo skill dataviz). Texto nunca usa a cor
  da série; séries ≥ 2 sempre com legenda.
- Lote: `create_transactions()` é tudo-ou-nada e reporta erros de todas as linhas (`BatchError`);
  formset com prefixo `linhas` (máx. 100) + cabeçalho `lote`; JS em `static/js/batch.js`.
- Menu lateral (desktop) é sempre compacto e expande por hover/foco — não há botão de recolher.
  A animação NÃO pode mudar o layout interno: o menu é montado com 256px, ícones centrados em
  x = 36px, e expandir = abrir `clip-path` + esmaecer textos (+ largura de botão/itens, que não
  afeta vizinhos). Mudanças ali devem manter as posições dos ícones idênticas nos dois estados.
- `[hidden]` tem `display: none !important` no CSS base: use o atributo para esconder elementos.
- Views tipam `request: AuthenticatedHttpRequest` (`apps/core/http.py`); objetos por id sempre via
  `get_object_or_404(<selector escopado>(request.user), pk=pk)` → 404 para dados de outro usuário.
- Cuidado com `@layer`: regras em `layout` perdem para `components` independentemente da
  especificidade; sobrescritas de componentes vão na camada `components`.
- Menu lateral recolhível (`data-sidebar="collapsed"` no `<html>`, salvo no localStorage).
- Portabilidade (`apps/portability`): mudou um modelo de dados do usuário? Atualize export
  (`selectors.export_payload`), import (`services._Importer`) e, se o formato mudar, suba
  `SCHEMA_VERSION` com um migrador em `schema.MIGRATIONS`. O teste de round-trip deve continuar
  verde. `manage.py backup` copia o SQLite (API de backup online) com rotação.
- Recorrências (`apps/planning`): `materialize(user, today)` gera previstas até hoje + 92 dias,
  preguiçosa e idempotente (chamada nas telas que mostram previstas). Datas em
  `planning/domain/schedule.py` (sempre a partir da data inicial). `generated_through` é o cursor:
  **nunca pode voltar** (senão ocorrências puladas reaparecem) — salve com `update_fields`. O
  formulário reaproveita `ledger/_entry_fields.html` e `ledger.views.category_groups`.
- Exclusão de movimentação é desfazível: `transaction_snapshot()` vai para a sessão e a mensagem
  ganha o botão "Desfazer" (`done(..., extra_tags="undo")` → `ledger:transaction_restore`).
- Dashboard: `?mes=AAAA-MM` escolhe um mês (navegação ◂ ▸, nunca futuro); usuário sem nenhuma
  receita/despesa vê `dashboard/_welcome.html` em vez de gráficos zerados.
- Dev: login automático no `runserver` (só DEBUG + localhost, `apps/core/dev_autologin.py`).

## Estrutura
`config/settings/{base,dev,test,prod}.py` · `apps/<app>/` (`models`, `services`, `selectors`,
`domain/`, `views`, `tests/`) · `templates/` · `static/` · `docs/`.
Apps de dados herdam `apps.core.models.OwnedModel`. Services recebem `apps.users.models.User`;
nomes de entidades passam por `apps.core.names` (exibição limpa + chave normalizada única).
Fixtures de teste compartilhadas (`user`, `other_user`) em `conftest.py`; todo service novo
precisa de teste de isolamento entre usuários.

## Dados
- Dev usa por padrão `db-demo.sqlite3` (fictício). `uv run python manage.py seed_demo` recria o
  usuário `demo` com ~6 meses de dados (`apps/ledger/demo.py`, determinístico). O comando recusa
  bancos cujo nome não contém "demo". **Nunca** gravar dados de teste no `db.sqlite3` (dados reais).

## Comandos
```bash
uv run python manage.py runserver     # dev local (settings.dev é o padrão do manage.py)
uv run pytest                         # testes (settings.test, sem migrações)
uv run ruff check . && uv run ruff format .
uv run mypy .                         # strict + django-stubs
uv run python manage.py makemigrations && uv run python manage.py migrate
```
Antes de concluir qualquer tarefa: pytest, ruff e mypy verdes. Se o mypy acusar de repente
"Couldn't resolve related manager"/campos de `OwnedModel`, é cache: `rm -rf .mypy_cache`.
Avisos de depreciação do Django quebram os testes de propósito (upgrade previsível).
Toda nova rota é coberta automaticamente por `apps/core/tests/test_access.py` (exige login);
rotas públicas precisam ser adicionadas explicitamente em `PUBLIC_URL_NAMES`.

## Git
- Commits em português, mensagem curta no imperativo. Push para `origin/main` (github.com/giovaniolivr/Moneta).
- **Atualizar o `README.md` a cada entrega** (checklist de status, comandos, stack).
- **Nunca** adicionar `Co-Authored-By` ou qualquer atribuição ao Claude em commits/PRs.
- Nunca commitar `.env`, `db.sqlite3` ou backups.
