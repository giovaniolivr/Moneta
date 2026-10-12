# Moneta

[![CI](https://github.com/giovaniolivr/Moneta/actions/workflows/ci.yml/badge.svg)](https://github.com/giovaniolivr/Moneta/actions/workflows/ci.yml)

Plataforma de gestão e planejamento financeiro pessoal: registro rápido de movimentações,
dashboard analítica e, no futuro, objetivos, simulação de investimentos e estratégias de compra.

Uso pessoal por enquanto, com arquitetura pronta para multiusuário e clientes web/mobile.
Detalhes técnicos em [`docs/ARQUITETURA.md`](docs/ARQUITETURA.md) e decisões em [`docs/adr/`](docs/adr/).

## Telas

![Dashboard no tema escuro](docs/screenshots/dashboard-dark.png)

| Tema claro | Lançamento em lote |
|---|---|
| ![Dashboard no tema claro](docs/screenshots/dashboard-light.png) | ![Lançamento em lote](docs/screenshots/batch-dark.png) |

<p>
  <img src="docs/screenshots/dashboard-mobile-dark.png" alt="Dashboard no celular" width="260">
  <img src="docs/screenshots/login-mobile-dark.png" alt="Login no celular" width="260">
</p>

_Capturas com dados fictícios._

## Stack

Python 3.14 · Django 6.1 · SQLite · CSS e gráficos SVG próprios (sem build, sem dependências JS) ·
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
- [x] 8. Motor de indicadores (comparação pro-rata, médias de meses fechados, detalhamentos)
- [x] 9. Dashboard com gráficos acessíveis, tabela equivalente e drill-down para a lista
- [x] 10. Análises automáticas (resumo, acima da média, tendência, concentração, dia da semana) com evidência numérica e aviso de dados insuficientes
- [x] 11. Seções secundárias da dashboard: comparativo mês a mês por categoria (com tendência e média), variação de tags e formas de pagamento
- [x] 12. Exportação JSON completa e CSV (Excel pt-BR), importação com prévia (idempotente, tudo ou nada) e backup do SQLite com rotação
- [x] 13. Polimento: desfazer exclusão, navegação entre meses na dashboard, primeiros passos para quem começa, páginas de erro e acessibilidade (PWA/mobile fica para a etapa de uso real)

**Fase 2 — planejamento**

- [x] 14. Movimentações previstas × realizadas: data futura vira prevista, tela "Previstas" com "A confirmar", aviso na dashboard; saldos e análises só com realizadas
- [x] 15. Recorrências (mensal, semanal, anual, a cada N): geram previstas para ~3 meses; editar, pausar, pular e excluir sem tocar no histórico confirmado
- [x] 16. Compras parceladas (2× a 48×) direto no formulário: cada parcela no seu mês, futuras previstas, "Confirmar todas" em A confirmar
- [x] 17. Orçamentos mensais (por categoria ou do total) com ritmo pelo seu próprio padrão de gasto, projeção do mês e alerta nas Análises
- [x] 18. Projeção de saldo dia a dia por conta, faturas previstas do cartão (fechamento, vencimento, conta pagadora) e alerta de saldo negativo
- [x] 19. Metas sobre o saldo de uma conta: aporte mensal necessário, ritmo real de aportes, capacidade de poupança e data estimada
- [x] 20. Dashboard realizado × previsto: resultado previsto do mês, saldo previsto, próximas previstas, orçamentos e metas; projeção com gasto do dia a dia estimado

**Fase 3 — investimentos e estratégias de compra**

- [x] 21. Núcleo de cálculo: dias úteis com feriados nacionais, CDI %, prefixado e poupança, IR regressivo e IOF por aporte, carteira com resgates PEPS
- [x] 22. Taxas de mercado: CDI, Selic e IPCA do Banco Central sob demanda (tela Investimentos ou `manage.py atualizar_taxas`) e valor manual
- [x] 23. Produtos de investimento (% do CDI, % da Selic, prefixado, poupança; IR/IOF ou isentos) com modelos prontos editáveis, rentabilidade estimada em 1 ano e produto da conta
- [x] 24. Rendimento estimado (líquido de IR) na projeção de saldo e nas metas: aporte necessário com juros compostos e data estimada
- [x] 25. Simulador de investimento: valor inicial + aportes + prazo, produtos lado a lado (bruto, IOF, IR por aporte, líquido, em dinheiro de hoje), gráfico e link compartilhável
- [x] 26. Estratégias de compra: à vista com desconto × parcelado sem/com juros × entrada + parcelas, com o dinheiro rendendo até cada pagamento, custo real em dinheiro de hoje e juros embutidos
- [x] 27. Estratégias de compra: juntar e comprar depois (quando e quanto economiza), quanto guardar por mês até uma data, "cabe no bolso?" contra sua capacidade e saldo, e meta pré-preenchida
- [x] 28. Polimento: atalhos para as estratégias de compra (dashboard e compra parcelada), recomendação mais legível, orientação quando ainda não há taxas

**Fase 4 — pesquisa de preços**

- [x] 29. Lista de desejos: preço-alvo, registro rápido (cole o link, a loja é reconhecida), melhor preço agora com frete, menor já visto, histórico e gráfico
- [ ] 30. Coletor do Mercado Livre (API oficial) — na etapa de configuração real, com as credenciais
- [x] 31. Alertas de preço nas Análises da dashboard: no alvo ou o menor dos últimos 60 dias, com evidência
- [x] 32. Da lista para as estratégias de compra (preço e parcelas preenchidos) e para uma meta
- [ ] 33. Polimento da Fase 4

**Futuro:** assistente com IA (Fase 5) — adiada; o foco agora é revisão, polimento e uso real.

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

## Uso real (PC + celular via Tailscale)

O Moneta roda no seu PC (perfil `config.settings.personal`: banco `db.sqlite3`, DEBUG desligado,
servidor waitress) e o celular acessa pela rede privada do [Tailscale](https://tailscale.com),
que fornece HTTPS — dá para instalar como app no celular (PWA).

1. `.env` com `DJANGO_SECRET_KEY` própria e `MONETA_HOSTS=<seu-pc>.<tailnet>.ts.net`.
2. Criar seu usuário no banco real:
   `$env:DJANGO_SETTINGS_MODULE="config.settings.personal"; uv run python manage.py migrate; uv run python manage.py createsuperuser`
3. Iniciar: `powershell -File scripts/serve.ps1` (migra, prepara os estáticos e serve em 127.0.0.1:8000).
4. Tailscale no PC e no celular (mesma conta) e, no PC: `tailscale serve --bg 8000`.
5. No celular, abra `https://<seu-pc>.<tailnet>.ts.net` e use "Adicionar à tela inicial".
6. Agendador de Tarefas do Windows: `scripts/serve.ps1` ao fazer logon e `scripts/backup.ps1` diário.

## Backup e portabilidade

- **Tela Dados** (`/dados/`): exportar tudo em JSON, movimentações em CSV e importar um JSON com
  prévia. Na lista de movimentações, "Exportar CSV" respeita os filtros aplicados.
- **Backup do banco:** `uv run python manage.py backup` grava uma cópia consistente em `backups/`
  (mantém as 10 mais recentes; `--destino PASTA`, `--manter N`). Para automatizar no Windows, crie
  uma tarefa diária no Agendador de Tarefas com esse comando, iniciando na pasta do projeto.
- **Restaurar:** com o servidor parado, `uv run python manage.py restaurar_backup backups/<arquivo>`
  mostra o que vai fazer; repita com `--confirmar`. Antes de restaurar, o banco atual é copiado para
  `backups/` (nada se perde se o backup escolhido for o errado).

## Qualidade

```bash
uv run pytest            # testes
uv run ruff check .      # lint
uv run ruff format .     # formatação
uv run mypy .            # tipagem
```
