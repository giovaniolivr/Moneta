// Alternância de tema escuro/claro. A escolha fica no navegador (localStorage); sem escolha,
// segue o sistema. O tema inicial é aplicado antes da pintura por um script inline no <head>.
(() => {
  const KEY = "moneta-theme";
  const root = document.documentElement;

  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-theme-toggle]");
    if (!button) return;
    const next = root.dataset.theme === "light" ? "dark" : "light";
    root.dataset.theme = next;
    try {
      localStorage.setItem(KEY, next);
    } catch {
      // Armazenamento indisponível (modo privado): vale só para esta página.
    }
  });
})();
