// Comportamento do formulário de movimentação (página inteira ou painel lateral).
// Usa delegação de eventos: funciona também no conteúdo injetado depois pelo painel.
(() => {
  // Formulário de conta: "Saldo inicial" vira "Fatura em aberto" no cartão de crédito.
  document.addEventListener("change", (event) => {
    const select = event.target;
    const form = select.closest("form[data-account-form]");
    if (form && select.name === "kind") form.dataset.kind = select.value;
  });

  document.addEventListener("change", (event) => {
    const input = event.target;
    const form = input.closest("form[data-transaction-form]");
    if (!form || input.name !== "kind") return;
    form.dataset.kind = input.value;
    // Desmarca a categoria se ela pertence ao tipo que ficou escondido.
    form.querySelectorAll('input[name="category"]:checked').forEach((radio) => {
      if (radio.closest("[data-for-kind]")?.dataset.forKind !== input.value) radio.checked = false;
    });
  });

  document.addEventListener("click", (event) => {
    const chip = event.target.closest("[data-add-tag]");
    if (!chip) return;
    const input = chip.closest("form")?.querySelector('input[name="tags"]');
    if (!input) return;
    const tags = input.value.split(",").map((t) => t.trim()).filter(Boolean);
    const name = chip.dataset.addTag;
    if (!tags.some((t) => t.toLowerCase() === name.toLowerCase())) tags.push(name);
    input.value = tags.join(", ");
    chip.setAttribute("aria-pressed", "true");
  });
})();
