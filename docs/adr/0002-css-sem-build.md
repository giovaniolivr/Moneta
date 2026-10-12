# ADR 0002 — CSS próprio com tokens, sem Tailwind

- Status: aceita (substitui a escolha de Tailwind feita na proposta inicial)
- Data: 2026-10-11

## Contexto
A proposta previa Tailwind v4 via `django-tailwind-cli`, que baixa um binário na primeira execução
e gera um CSS que precisa ser recompilado a cada mudança de template. Requisitos relevantes:
dependências mínimas, rodar com um simples `runserver`, temas escuro/claro sem texto invisível
e política de segurança (CSP) estrita, sem estilos inline.

## Decisão
Uma folha de estilos única (`static/css/app.css`) em CSS moderno, sem build:
- **Tokens** como variáveis CSS, redefinidos por tema (`:root[data-theme="dark|light"]`).
- `@layer` (tokens → base → layout → components → utilities) para controlar a cascata.
- Componentes por classes semânticas (`.btn`, `.card`, `.list-item`, `.amount-income`...).
- Paleta categórica em 8 posições (`--color-chart-1..8`), usada por categorias e gráficos.
- Fonte Inter e ícones Lucide servidos localmente (CSP `'self'`, funciona offline).

## Consequências
- Zero dependências e zero etapa de build; `git clone` + `runserver` já mostra o visual final.
- `apps/core/tests/test_theme_contrast.py` lê os tokens do próprio CSS e exige WCAG AA
  (4.5:1 texto, 3:1 gráficos) nos dois temas — trocar uma cor que quebre o contraste falha o CI.
- `apps/core/tests/test_ui.py` impede `style=""`, handlers `on*=` e `<script>` inline sem nonce.
- Custo: escrever as classes à mão. Aceitável para o número de telas previsto; se crescer muito,
  Tailwind pode ser adotado depois consumindo os mesmos tokens.
