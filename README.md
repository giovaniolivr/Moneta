# Moneta

[![CI](https://github.com/giovaniolivr/Moneta/actions/workflows/ci.yml/badge.svg)](https://github.com/giovaniolivr/Moneta/actions/workflows/ci.yml)

Plataforma de gestão e planejamento financeiro pessoal: registro rápido de movimentações,
dashboard analítica e, no futuro, objetivos, simulação de investimentos e estratégias de compra.

Uso pessoal por enquanto, com arquitetura pronta para multiusuário e clientes web/mobile.
Detalhes técnicos em [`docs/ARQUITETURA.md`](docs/ARQUITETURA.md) e decisões em [`docs/adr/`](docs/adr/).

## Stack

Python 3.14 · Django 6.1 · SQLite · HTMX + Alpine.js · Tailwind v4 · Apache ECharts ·
pytest · ruff · mypy (strict)

## Status

**Fase 1 — MVP (registro financeiro e dashboard)**

- [x] 0. Esqueleto: settings por ambiente, usuário customizado, login obrigatório, CSP, testes
- [x] 1. Valores monetários (centavos ↔ R$): parser tolerante, formatação BRL, campo de formulário
- [x] 2. Contas e categorias (padrões criados por usuário, nomes únicos sem caixa/acentos)
- [ ] 3. Tags com normalização, aliases e mesclagem
- [ ] 4. Movimentações (receitas, despesas, transferências)
- [ ] 5. Layout base e temas escuro/claro
- [ ] 6. Registro rápido de movimentação
- [ ] 7. Lista e filtros de movimentações
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
uv run python manage.py createsuperuser
uv run python manage.py runserver
```

Acesse http://127.0.0.1:8000 — não há cadastro público; entre com o usuário criado acima.

## Qualidade

```bash
uv run pytest            # testes
uv run ruff check .      # lint
uv run ruff format .     # formatação
uv run mypy .            # tipagem
```
