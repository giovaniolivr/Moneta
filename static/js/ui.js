// Preferências de interface guardadas no navegador: tema e menu lateral recolhido.
// O estado inicial é aplicado antes da pintura por um script inline no <head> (base.html).
(() => {
  const root = document.documentElement;

  function save(key, value) {
    try {
      localStorage.setItem(key, value);
    } catch {
      // Armazenamento indisponível (modo privado): vale só para esta página.
    }
  }

  document.querySelectorAll("[data-sidebar-toggle]").forEach((button) => {
    button.setAttribute("aria-expanded", String(root.dataset.sidebar !== "collapsed"));
  });

  // Formulários de filtro (GET): não envia campos vazios, para a URL ficar limpa.
  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (form.method !== "get" || !form.matches("[data-clean-query]")) return;
    form.querySelectorAll("input, select").forEach((field) => {
      if (field.name && !field.value && field.type !== "checkbox") field.disabled = true;
    });
  });

  // Ao voltar pelo histórico (cache do navegador), reabilita os campos desativados acima.
  window.addEventListener("pageshow", () => {
    document.querySelectorAll("[data-clean-query] :disabled").forEach((field) => {
      field.disabled = false;
    });
  });

  document.addEventListener("click", (event) => {
    if (event.target.closest("[data-theme-toggle]")) {
      const next = root.dataset.theme === "light" ? "dark" : "light";
      root.dataset.theme = next;
      save("moneta-theme", next);
    }
    const sidebarToggle = event.target.closest("[data-sidebar-toggle]");
    if (sidebarToggle) {
      const next = root.dataset.sidebar === "collapsed" ? "expanded" : "collapsed";
      root.dataset.sidebar = next;
      sidebarToggle.setAttribute("aria-expanded", String(next === "expanded"));
      save("moneta-sidebar", next);
    }
  });
})();
