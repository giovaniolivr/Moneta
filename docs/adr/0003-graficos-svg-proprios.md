# ADR 0003 — Gráficos em SVG próprio em vez de Apache ECharts

- Status: aceita (substitui a escolha de ECharts feita na proposta inicial)
- Data: 2026-10-11

## Contexto
A dashboard precisa de poucos tipos de gráfico (colunas agrupadas, colunas divergentes, linhas
acumuladas e barras horizontais), com requisitos estritos:
- especificações de visualização (barras ≤ 24px com ponta arredondada, linhas de 2px, grade em
  hairline, tooltip com valor em destaque, foco por teclado igual ao hover, tabela equivalente);
- paleta categórica validada para daltonismo nos dois temas;
- troca de tema instantânea e CSP estrita (`'self'`, sem estilos inline);
- dependências mínimas.

## Decisão
`static/js/charts.js` (~300 linhas, sem dependências) desenha SVG a partir de JSON gerado pelo
servidor (`json_script`). Cores vêm de classes CSS ligadas aos tokens do tema; barras horizontais
são HTML + SVG renderizados no servidor (funcionam sem JavaScript). Todo gráfico tem uma tabela
equivalente em `<details>`.

Paleta categórica: a instância validada do método de dataviz (slots 1–8, passos próprios para o
tema escuro), verificada com o script de validação contra as superfícies do Moneta. No tema
claro, os slots 3, 4 e 5 ficam abaixo de 3:1 por desenho e exigem rótulo visível/tabela
(garantido nos templates e coberto por teste de contraste).

## Consequências
- Zero dependências novas; ~10 KB de JS; nada de `eval` ou estilos inline.
- Novos tipos de gráfico exigem código próprio. Se a dashboard crescer para mapas, heatmaps ou
  muitos tipos, reavaliar uma biblioteca (mantendo os mesmos tokens e regras).
