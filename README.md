# Moneta

[![CI](https://github.com/giovaniolivr/Moneta/actions/workflows/ci.yml/badge.svg)](https://github.com/giovaniolivr/Moneta/actions/workflows/ci.yml)

Plataforma de gestão e planejamento financeiro pessoal: registro rápido de movimentações,
dashboard analítica e, no futuro, objetivos, simulação de investimentos e estratégias de compra.

Uso pessoal por enquanto, com arquitetura pronta para multiusuário e clientes web/mobile.
Detalhes técnicos em [`docs/ARQUITETURA.md`](docs/ARQUITETURA.md) e decisões em [`docs/adr/`](docs/adr/).

## Telas

| Escuro | Claro |
|---|---|
| ![Início no tema escuro](docs/screenshots/home-desktop-dark.png) | ![Início no tema claro](docs/screenshots/home-desktop-light.png) |

<p>
  <img src="docs/screenshots/home-mobile-dark.png" alt="Início no celular" width="260">
  <img src="docs/screenshots/login-mobile-dark.png" alt="Login no celular" width="260">
</p>

_Capturas com dados fictícios._

## Stack

Python 3.14 · Django 6.1 · SQLite · HTMX · CSS próprio (sem build) · Apache ECharts ·
pytest · ruff · mypy (strict)

## Status

**Fase 1 — MVP (registro financeiro e dashboard)**

- [x] 0. Esqueleto: settings por ambiente, usuário customizado, login obrigatório, CSP, testes
- [x] 1. Valores monetários (centavos ↔ R$): parser tolerante, formatação BRL, campo de formulário
- [x] 2. Contas e categorias (padrões criados por usuário, nomes únicos sem caixa/acentos)
- [x] 3. Tags com normalização, apelidos, mesclagem explícita e busca para autocomplete
- [x] 4. Movimentações (receitas, despesas, transferências) com regras garantidas no banco e saldo por conta
- [x] 5. Layout base e temas escuro/claro (CSS próprio sem build, contraste WCAG AA testado)
- [x] 6. Registro rápido de movimentação em painel lateral (celular: painel inferior)
- [x] 6b. Lançamento em lote: várias movimentações numa tela, totais ao vivo, rascunho salvo, grava tudo ou nada
- [x] 7. Lista com filtros combináveis (período, tipo, categoria, tag, conta, pagamento, busca), edição e exclusão
- [x] 7b. Telas de categorias (cor e ícone), tags (apelidos, mesclar) e contas (saldo líquido, cartão como dívida)
- [ ] 8. Motor de indicadores
- [ ] 9. Dashboard com gráficos e drill-down
- [ ] 10. Análises automáticas
- [ ] 11. Seções secundárias da dashboard
- [ ] 12. Exportação, importação e backup
- [ ] 13. Polimento, acessibilidade e PWA

**Próximas fases:** planejamento (metas, recorrências, parcelamentos, orçamentos) →
simulador de investimentos e estratégias de compra → pesquisa de preços → assistente com IA.

## Rodando localmente

Requer [uv](https://docs.astral.sh/uv/) (instala o Python 3.14 automaticamente).

```bash
uv sync
cp .env.example .env            # e gere uma DJANGO_SECRET_KEY (instrução no arquivo)
uv run python manage.py migrate
uv run python manage.py seed_demo   # ~6 meses de dados fictícios (usuário "demo")
uv run python manage.py runserver
```

Em desenvolvimento o banco padrão é o **`db-demo.sqlite3`, só com dados fictícios** (a interface
mostra o aviso "Dados fictícios"). `seed_demo` pode ser rodado de novo a qualquer momento para
recriar os dados. Para usar dados reais, aponte `DATABASE_URL=sqlite:///db.sqlite3` no `.env` e
crie seu usuário com `createsuperuser`.

Acesse http://127.0.0.1:8000 — em desenvolvimento o login é automático (somente localhost; desative com
`DEV_AUTO_LOGIN=0` no `.env`). Não há cadastro público.

## Qualidade

```bash
uv run pytest            # testes
uv run ruff check .      # lint
uv run ruff format .     # formatação
uv run mypy .            # tipagem
```
