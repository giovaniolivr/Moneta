# Moneta

Plataforma de gestão e planejamento financeiro pessoal. Uso próprio no início, arquitetada para
virar multiusuário e ganhar clientes web/mobile. Idioma do produto e da documentação: pt-BR.
Código (identificadores) em inglês.

**Status:** arquitetura validada; entrega 0 (esqueleto) concluída. Seguir o roadmap da §10 de
`docs/ARQUITETURA.md`, uma entrega por vez.

## Documentos de referência
- `docs/ARQUITETURA.md` — stack, camadas, modelagem, roadmap, decisões. Fonte da verdade técnica.
- `docs/adr/` — registro de decisões (um arquivo por decisão relevante).
- `DESIGN.md` — **inspiração** visual (modo escuro, tipografia, tamanhos), não especificação.
  Pode ser ajustado. No código, usar sempre tokens (variáveis CSS), nunca hex soltos. Temas
  escuro (padrão) e claro; todo par texto/fundo deve passar contraste WCAG AA nos dois.

## Stack
Python 3.14 (uv) · Django 6.1 → 6.2 LTS (ADR 0001) · SQLite (Postgres no futuro) · HTMX ·
CSS próprio sem build (ADR 0002) · Apache ECharts · pytest · ruff · mypy.

## Regras inegociáveis
- **Dinheiro em centavos inteiros** (`amount_cents`, `BigIntegerField`). Nunca `float`.
  `amount_cents > 0`; o sinal vem de `kind` (income/expense/transfer).
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
- Formulários rápidos: links com `data-drawer` abrem a URL no painel (`static/js/drawer.js`).
  A view devolve só o fragmento quando há o header `X-Moneta-Fragment: 1`, e `204` +
  `X-Moneta-Event` ao salvar; sem JS, a mesma URL renderiza a página inteira.
- Views tipam `request: AuthenticatedHttpRequest` (`apps/core/http.py`).
- Dev: login automático no `runserver` (só DEBUG + localhost, `apps/core/dev_autologin.py`).

## Estrutura
`config/settings/{base,dev,test,prod}.py` · `apps/<app>/` (`models`, `services`, `selectors`,
`domain/`, `views`, `tests/`) · `templates/` · `static/` · `docs/`.
Apps de dados herdam `apps.core.models.OwnedModel`. Services recebem `apps.users.models.User`;
nomes de entidades passam por `apps.core.names` (exibição limpa + chave normalizada única).
Fixtures de teste compartilhadas (`user`, `other_user`) em `conftest.py`; todo service novo
precisa de teste de isolamento entre usuários.

## Comandos
```bash
uv run python manage.py runserver     # dev local (settings.dev é o padrão do manage.py)
uv run pytest                         # testes (settings.test, sem migrações)
uv run ruff check . && uv run ruff format .
uv run mypy .                         # strict + django-stubs
uv run python manage.py makemigrations && uv run python manage.py migrate
```
Antes de concluir qualquer tarefa: pytest, ruff e mypy verdes.
Avisos de depreciação do Django quebram os testes de propósito (upgrade previsível).
Toda nova rota é coberta automaticamente por `apps/core/tests/test_access.py` (exige login);
rotas públicas precisam ser adicionadas explicitamente em `PUBLIC_URL_NAMES`.

## Git
- Commits em português, mensagem curta no imperativo. Push para `origin/main` (github.com/giovaniolivr/Moneta).
- **Atualizar o `README.md` a cada entrega** (checklist de status, comandos, stack).
- **Nunca** adicionar `Co-Authored-By` ou qualquer atribuição ao Claude em commits/PRs.
- Nunca commitar `.env`, `db.sqlite3` ou backups.
