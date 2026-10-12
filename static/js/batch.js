// Lançamento em lote: linhas dinâmicas (formset do Django), totais ao vivo e rascunho local.
// Sem JavaScript a página ainda funciona com as linhas iniciais renderizadas pelo servidor.
(() => {
  const form = document.querySelector("form[data-batch]");
  if (!form) return;

  const PREFIX = "linhas";
  const DRAFT_KEY = "moneta-batch-draft";
  const SUBMITTED_KEY = "moneta-batch-submitted";
  const rowsEl = form.querySelector("[data-batch-rows]");
  const template = document.getElementById("batch-row-template");
  const total = form.querySelector(`input[name="${PREFIX}-TOTAL_FORMS"]`);
  const maxRows = Number(form.dataset.maxRows || 100);
  const fields = ["kind", "amount", "category", "description", "tags", "date", "account"];

  const rows = () => [...rowsEl.querySelectorAll("[data-batch-row]")];
  const field = (row, name) => row.querySelector(`[name$="-${name}"]`);

  // ── Valores monetários (mesma regra de apps/core/money.py, só para exibir totais) ──
  function parseCents(text) {
    let s = (text || "").replace(/r\$/i, "").replace(/\s/g, "");
    if (!s) return 0;
    if (s.includes(",")) s = s.replace(/\./g, "").replace(",", ".");
    else if (!/^\d+\.\d{1,2}$/.test(s)) s = s.replace(/\./g, "");
    const value = Number(s);
    return Number.isFinite(value) && value > 0 ? Math.round(value * 100) : 0;
  }
  const brl = (cents) =>
    (cents / 100).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });

  // ── Linhas ──
  function renumber() {
    rows().forEach((row, index) => {
      row.querySelectorAll("[name], [id], label[for]").forEach((el) => {
        for (const attr of ["name", "id", "for"]) {
          const value = el.getAttribute(attr);
          if (value) el.setAttribute(attr, value.replace(/linhas-(\d+|__prefix__)-/, `${PREFIX}-${index}-`));
        }
      });
    });
    total.value = rows().length;
  }

  function addRow({ focus = false, values = null } = {}) {
    if (rows().length >= maxRows) return null;
    const fragment = template.content.cloneNode(true);
    const row = fragment.querySelector("[data-batch-row]");
    rowsEl.appendChild(fragment);
    renumber();
    if (values) fillRow(row, values);
    syncKind(row);
    if (focus) field(row, "amount").focus();
    return row;
  }

  function removeRow(row) {
    row.remove();
    if (!rows().length) addRow();
    renumber();
    update();
  }

  function isBlank(row) {
    return fields.slice(1).every((name) => !(field(row, name)?.value || "").trim());
  }

  function syncKind(row) {
    const kind = field(row, "kind").value;
    row.dataset.kind = kind;
    const category = field(row, "category");
    for (const option of category.querySelectorAll("option[data-kind]")) {
      option.hidden = option.disabled = option.dataset.kind !== kind;
    }
    if (category.selectedOptions[0]?.disabled) category.value = "";
  }

  // ── Totais ──
  function update() {
    let count = 0;
    let expense = 0;
    let income = 0;
    for (const row of rows()) {
      if (isBlank(row)) continue;
      count += 1;
      const cents = parseCents(field(row, "amount").value);
      if (field(row, "kind").value === "income") income += cents;
      else expense += cents;
    }
    form.querySelector("[data-batch-count]").textContent = count;
    form.querySelector("[data-batch-expense]").textContent = expense ? `-${brl(expense)}` : brl(0);
    form.querySelector("[data-batch-income]").textContent = income ? `+${brl(income)}` : brl(0);
    form.querySelector("[data-batch-submit]").textContent =
      count ? `Salvar ${count} lançamento${count > 1 ? "s" : ""}` : "Salvar tudo";
    saveDraft();
  }

  // ── Rascunho no navegador ──
  function readRow(row) {
    return Object.fromEntries(fields.map((name) => [name, field(row, name)?.value || ""]));
  }
  function fillRow(row, values) {
    for (const name of fields) {
      const el = field(row, name);
      if (el && values[name] !== undefined) el.value = values[name];
    }
  }
  function saveDraft() {
    const data = rows().filter((row) => !isBlank(row)).map(readRow);
    try {
      if (data.length) localStorage.setItem(DRAFT_KEY, JSON.stringify(data));
      else localStorage.removeItem(DRAFT_KEY);
    } catch {}
  }
  function restoreDraft() {
    let draft = null;
    try {
      if (sessionStorage.getItem(SUBMITTED_KEY)) {
        // O último envio deu certo (voltamos a uma página nova): o rascunho já foi salvo.
        localStorage.removeItem(DRAFT_KEY);
        sessionStorage.removeItem(SUBMITTED_KEY);
        return;
      }
      draft = JSON.parse(localStorage.getItem(DRAFT_KEY) || "null");
    } catch {}
    if (!Array.isArray(draft) || !draft.length) return;
    rows().forEach((row) => row.remove());
    draft.forEach((values) => addRow({ values }));
    addRow();
    form.querySelector("[data-batch-draft-notice]").hidden = false;
  }

  // ── Eventos ──
  form.addEventListener("input", (event) => {
    const row = event.target.closest("[data-batch-row]");
    if (row && event.target.matches("[data-batch-amount]") && row === rows().at(-1) && event.target.value) {
      addRow(); // sempre há uma linha vazia no fim
    }
    update();
  });

  form.addEventListener("change", (event) => {
    if (event.target.matches("[data-batch-kind]")) syncKind(event.target.closest("[data-batch-row]"));
    update();
  });

  form.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.target.tagName === "BUTTON") return;
    const row = event.target.closest("[data-batch-row]");
    if (!row) return;
    event.preventDefault(); // Enter não envia o formulário: vai para a próxima linha
    const next = row.nextElementSibling || addRow();
    if (next) field(next, "amount").focus();
  });

  form.addEventListener("click", (event) => {
    if (event.target.closest("[data-batch-add]")) addRow({ focus: true });
    const remove = event.target.closest("[data-batch-remove]");
    if (remove) removeRow(remove.closest("[data-batch-row]"));
    if (event.target.closest("[data-batch-discard]")) {
      try {
        localStorage.removeItem(DRAFT_KEY);
      } catch {}
      window.location.reload();
    }
  });

  form.addEventListener("submit", () => {
    try {
      sessionStorage.setItem(SUBMITTED_KEY, "1");
    } catch {}
  });

  // ── Início ──
  if (form.dataset.batchFresh !== undefined) {
    restoreDraft();
  } else {
    try {
      sessionStorage.removeItem(SUBMITTED_KEY); // voltou com erros: o rascunho continua valendo
    } catch {}
  }
  rows().forEach(syncKind);
  if (!rows().length || !isBlank(rows().at(-1))) addRow();
  update();
})();
