// Gráficos da dashboard em SVG puro (sem dependências; ADR 0003).
// Cada [data-chart="tipo"] lê seus dados de um <script type="application/json" id="…">
// apontado por data-source. Cores vêm de classes CSS (tokens do tema), então trocar o tema não
// exige redesenhar. Especificações seguem o skill de dataviz: barras ≤ 24px com ponta
// arredondada de 4px, linhas de 2px, grade em hairline, tooltip com valor em destaque,
// foco por teclado igual ao hover e tabela equivalente renderizada pelo servidor.
(() => {
  const SVG = "http://www.w3.org/2000/svg";
  const PLOT_HEIGHT = 220;
  const AXIS_BAND = 28;
  const LEFT = 64;
  const RIGHT = 12;
  const TOP = 12;
  const BAR_MAX = 24;
  const GAP = 2;

  const brl = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
  const brlCompact = new Intl.NumberFormat("pt-BR", {
    style: "currency",
    currency: "BRL",
    notation: "compact",
    maximumFractionDigits: 1,
  });
  const money = (cents) => brl.format(cents / 100);
  const moneyAxis = (cents) => (cents === 0 ? "R$ 0" : brlCompact.format(cents / 100));

  function el(name, attrs = {}, parent = null) {
    const node = document.createElementNS(SVG, name);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    if (parent) parent.appendChild(node);
    return node;
  }

  // Escala "redonda": 4 divisões com passo 1/2/2,5/5 × 10^n.
  function niceScale(min, max) {
    const span = Math.max(max - min, 1);
    const raw = span / 4;
    const power = 10 ** Math.floor(Math.log10(raw));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * power).find((s) => s >= raw);
    const lo = Math.floor(min / step) * step;
    const hi = Math.ceil(max / step) * step;
    const ticks = [];
    for (let v = lo; v <= hi + step / 2; v += step) ticks.push(Math.round(v));
    return { lo, hi, ticks };
  }

  // Barra com ponta arredondada (4px) e base reta, crescendo de `base` até `value`.
  function barPath(x, width, yBase, yValue) {
    const up = yValue <= yBase;
    const h = Math.abs(yBase - yValue);
    const r = Math.min(4, width / 2, h);
    if (h < 0.5) return "";
    if (up) {
      return `M${x},${yBase} V${yValue + r} Q${x},${yValue} ${x + r},${yValue} H${x + width - r} Q${x + width},${yValue} ${x + width},${yValue + r} V${yBase} Z`;
    }
    return `M${x},${yBase} V${yValue - r} Q${x},${yValue} ${x + r},${yValue} H${x + width - r} Q${x + width},${yValue} ${x + width},${yValue - r} V${yBase} Z`;
  }

  // ── Tooltip (conteúdo via textContent: rótulos são dados) ──
  function tooltip(container) {
    const tip = document.createElement("div");
    tip.className = "viz-tooltip";
    tip.hidden = true;
    container.appendChild(tip);
    return {
      show(title, rows, x, y) {
        tip.replaceChildren();
        const head = document.createElement("div");
        head.className = "viz-tooltip-title";
        head.textContent = title;
        tip.appendChild(head);
        for (const row of rows) {
          const line = document.createElement("div");
          line.className = "viz-tooltip-row";
          const key = document.createElement("span");
          key.className = `viz-key ${row.cls}`;
          const value = document.createElement("strong");
          value.textContent = row.value;
          const label = document.createElement("span");
          label.className = "viz-tooltip-label";
          label.textContent = row.label;
          line.append(key, value, label);
          tip.appendChild(line);
        }
        tip.hidden = false;
        const box = container.getBoundingClientRect();
        const left = Math.min(Math.max(x + 12, 0), box.width - tip.offsetWidth - 4);
        tip.style.left = `${Math.max(left, 4)}px`;
        tip.style.top = `${Math.max(y - tip.offsetHeight - 8, 0)}px`;
      },
      hide() {
        tip.hidden = true;
      },
    };
  }

  function frame(container, ticks, yOf, width) {
    container.querySelector("svg")?.remove();
    const height = PLOT_HEIGHT + AXIS_BAND + TOP;
    const svg = el("svg", {
      viewBox: `0 0 ${width} ${height}`,
      width,
      height,
      class: "viz-svg",
      role: "img",
      "aria-label": container.dataset.label || "Gráfico",
    });
    container.prepend(svg);
    for (const t of ticks) {
      const y = yOf(t);
      el("line", { x1: LEFT, x2: width - RIGHT, y1: y, y2: y, class: t === 0 ? "viz-baseline" : "viz-grid" }, svg);
      const label = el("text", { x: LEFT - 8, y: y + 4, class: "viz-axis", "text-anchor": "end" }, svg);
      label.textContent = moneyAxis(t);
    }
    return svg;
  }

  // Rótulos do eixo X nunca se sobrepõem: em colunas estreitas, mostra um a cada N.
  const labelStep = (band) => Math.max(1, Math.ceil(52 / band));

  function xLabels(svg, labels, xCenter, every = 1) {
    labels.forEach((text, i) => {
      if (i % every) return;
      const label = el("text", {
        x: xCenter(i),
        y: TOP + PLOT_HEIGHT + 18,
        class: "viz-axis",
        "text-anchor": "middle",
      }, svg);
      label.textContent = text;
    });
  }

  // Área de interação por coluna (maior que a marca): hover, foco e clique para a lista.
  function bands(svg, data, x0, bandWidth, tip, rowsAt, links) {
    data.labels.forEach((label, i) => {
      const hit = el("rect", {
        x: x0(i),
        y: TOP,
        width: bandWidth,
        height: PLOT_HEIGHT,
        class: "viz-hit",
        tabindex: "0",
        role: links ? "link" : "img",
      }, svg);
      const rows = rowsAt(i);
      hit.setAttribute("aria-label", `${label}: ${rows.map((r) => `${r.label} ${r.value}`).join(", ")}`);
      const show = () => {
        svg.querySelectorAll(".is-active").forEach((n) => n.classList.remove("is-active"));
        svg.querySelectorAll(`[data-index="${i}"]`).forEach((n) => n.classList.add("is-active"));
        tip.show(label, rows, x0(i) + bandWidth / 2, TOP + 24);
      };
      hit.addEventListener("pointerenter", show);
      hit.addEventListener("focus", show);
      hit.addEventListener("pointerleave", () => tip.hide());
      hit.addEventListener("blur", () => tip.hide());
      if (links?.[i]) {
        const go = () => (window.location.href = links[i]);
        hit.addEventListener("click", go);
        hit.addEventListener("keydown", (e) => e.key === "Enter" && go());
      }
    });
  }

  // ── Colunas agrupadas (ex.: receitas × despesas por mês) ──
  function columns(container, data, width) {
    const all = data.series.flatMap((s) => s.values);
    const { lo, hi, ticks } = niceScale(0, Math.max(...all, 1));
    const yOf = (v) => TOP + PLOT_HEIGHT - ((v - lo) / (hi - lo)) * PLOT_HEIGHT;
    const svg = frame(container, ticks, yOf, width);
    const band = (width - LEFT - RIGHT) / data.labels.length;
    const n = data.series.length;
    const bar = Math.min(BAR_MAX, (band * 0.7 - GAP * (n - 1)) / n);
    const group = bar * n + GAP * (n - 1);
    data.labels.forEach((_, i) => {
      const start = LEFT + i * band + (band - group) / 2;
      data.series.forEach((s, k) => {
        const d = barPath(start + k * (bar + GAP), bar, yOf(0), yOf(s.values[i]));
        if (d) el("path", { d, class: `viz-mark viz-${s.key}`, "data-index": i }, svg);
      });
    });
    xLabels(svg, data.labels, (i) => LEFT + i * band + band / 2, labelStep(band));
    const tip = tooltip(container);
    bands(svg, data, (i) => LEFT + i * band, band, tip,
      (i) => data.series.map((s) => ({ cls: `viz-${s.key}`, label: s.label, value: money(s.values[i]) })),
      data.links);
  }

  // ── Colunas divergentes em torno de zero (ex.: resultado mensal) ──
  function diverging(container, data, width) {
    const { lo, hi, ticks } = niceScale(Math.min(0, ...data.values), Math.max(0, ...data.values, 1));
    const yOf = (v) => TOP + PLOT_HEIGHT - ((v - lo) / (hi - lo)) * PLOT_HEIGHT;
    const svg = frame(container, ticks, yOf, width);
    const band = (width - LEFT - RIGHT) / data.labels.length;
    const bar = Math.min(BAR_MAX, band * 0.6);
    data.values.forEach((v, i) => {
      const d = barPath(LEFT + i * band + (band - bar) / 2, bar, yOf(0), yOf(v));
      if (d) el("path", { d, class: `viz-mark ${v >= 0 ? "viz-pos" : "viz-neg"}`, "data-index": i }, svg);
    });
    xLabels(svg, data.labels, (i) => LEFT + i * band + band / 2, labelStep(band));
    const tip = tooltip(container);
    bands(svg, data, (i) => LEFT + i * band, band, tip,
      (i) => [{ cls: data.values[i] >= 0 ? "viz-pos" : "viz-neg", label: "Resultado",
        value: (data.values[i] > 0 ? "+" : "") + money(data.values[i]) }],
      data.links);
  }

  // ── Linhas acumuladas com cursor vertical (ex.: ritmo de gastos do mês) ──
  function lines(container, data, width) {
    const all = data.series.flatMap((s) => s.values);
    const { lo, hi, ticks } = niceScale(0, Math.max(...all, 1));
    const yOf = (v) => TOP + PLOT_HEIGHT - ((v - lo) / (hi - lo)) * PLOT_HEIGHT;
    const svg = frame(container, ticks, yOf, width);
    const count = data.labels.length;
    const xOf = (i) => LEFT + (count > 1 ? (i / (count - 1)) * (width - LEFT - RIGHT) : 0);
    for (const s of data.series) {
      if (!s.values.length) continue;
      const d = s.values.map((v, i) => `${i ? "L" : "M"}${xOf(i).toFixed(1)},${yOf(v).toFixed(1)}`).join(" ");
      el("path", { d, class: `viz-line viz-${s.key}` }, svg);
    }
    // Ponto e rótulo direto só no fim da série atual (rótulo seletivo).
    const current = data.series.at(-1);
    if (current?.values.length) {
      const i = current.values.length - 1;
      el("circle", { cx: xOf(i), cy: yOf(current.values[i]), r: 4, class: `viz-dot viz-${current.key}` }, svg);
      const label = el("text", {
        x: Math.min(xOf(i) + 8, width - RIGHT),
        y: yOf(current.values[i]) - 8,
        class: "viz-direct-label",
        "text-anchor": xOf(i) > width - 120 ? "end" : "start",
      }, svg);
      label.textContent = money(current.values[i]);
    }
    const every = Math.ceil(count / 8);
    xLabels(svg, data.labels, xOf, every);

    const tip = tooltip(container);
    const cross = el("line", { y1: TOP, y2: TOP + PLOT_HEIGHT, class: "viz-crosshair", visibility: "hidden" }, svg);
    const hit = el("rect", { x: LEFT, y: TOP, width: width - LEFT - RIGHT, height: PLOT_HEIGHT, class: "viz-hit", tabindex: "0" }, svg);
    hit.setAttribute("aria-label", "Use as setas para percorrer os dias");
    let index = count - 1;
    const showAt = (i) => {
      index = Math.max(0, Math.min(count - 1, i));
      const x = xOf(index);
      cross.setAttribute("x1", x);
      cross.setAttribute("x2", x);
      cross.setAttribute("visibility", "visible");
      const rows = data.series
        .filter((s) => index < s.values.length)
        .map((s) => ({ cls: `viz-${s.key}`, label: s.label, value: money(s.values[index]) }))
        .reverse();
      tip.show(`Dia ${data.labels[index]}`, rows, x, TOP + 24);
    };
    hit.addEventListener("pointermove", (e) => {
      const box = svg.getBoundingClientRect();
      const x = ((e.clientX - box.left) / box.width) * width;
      showAt(Math.round(((x - LEFT) / (width - LEFT - RIGHT)) * (count - 1)));
    });
    hit.addEventListener("focus", () => showAt(index));
    hit.addEventListener("keydown", (e) => {
      if (e.key === "ArrowRight") showAt(index + 1);
      if (e.key === "ArrowLeft") showAt(index - 1);
    });
    const hide = () => {
      cross.setAttribute("visibility", "hidden");
      tip.hide();
    };
    hit.addEventListener("pointerleave", hide);
    hit.addEventListener("blur", hide);
  }

  const RENDERERS = { columns, diverging, lines };

  function render(container) {
    const source = document.getElementById(container.dataset.source);
    const renderer = RENDERERS[container.dataset.chart];
    if (!source || !renderer) return;
    const data = JSON.parse(source.textContent);
    const width = Math.max(container.clientWidth, 280);
    container.querySelector(".viz-tooltip")?.remove();
    renderer(container, data, width);
  }

  const charts = document.querySelectorAll("[data-chart]");
  charts.forEach(render);
  let lastWidths = new Map();
  const observer = new ResizeObserver((entries) => {
    for (const entry of entries) {
      const width = Math.round(entry.contentRect.width);
      if (lastWidths.get(entry.target) !== width) {
        lastWidths.set(entry.target, width);
        render(entry.target);
      }
    }
  });
  charts.forEach((chart) => observer.observe(chart));
})();
