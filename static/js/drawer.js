// Painel lateral (no celular, painel inferior) para formulários rápidos.
// Links com [data-drawer] abrem o conteúdo da URL num <dialog>; sem JavaScript, o link
// leva à página inteira do formulário (mesmo template). Contrato em apps/core/drawer.py.
(() => {
  const dialog = document.getElementById("drawer");
  if (!dialog) return;
  const body = dialog.querySelector("[data-drawer-body]");
  const FRAGMENT = { "X-Moneta-Fragment": "1" };
  let changed = false; // algo foi salvo com o painel aberto: recarrega ao fechar

  async function load(url, options = {}) {
    let response;
    try {
      response = await fetch(url, {
        ...options,
        headers: { ...FRAGMENT, ...(options.headers || {}) },
        credentials: "same-origin",
      });
    } catch {
      window.location.href = url;
      return;
    }
    if (response.status === 204) {
      changed = true;
      dialog.close();
      return;
    }
    if (response.headers.has("X-Moneta-Event")) changed = true;
    if (!response.ok) {
      window.location.href = url;
      return;
    }
    body.innerHTML = await response.text();
    body.querySelector("[autofocus]")?.focus();
  }

  document.addEventListener("click", (event) => {
    const link = event.target.closest("a[data-drawer]");
    if (!link || event.metaKey || event.ctrlKey || event.shiftKey) return;
    event.preventDefault();
    body.innerHTML = '<p class="drawer-loading">Carregando…</p>';
    dialog.showModal();
    load(link.href);
  });

  dialog.addEventListener("submit", (event) => {
    const form = event.target.closest("form");
    if (!form) return;
    event.preventDefault();
    const data = new FormData(form);
    if (event.submitter?.name) data.append(event.submitter.name, event.submitter.value);
    load(form.action, { method: "POST", body: data });
  });

  dialog.addEventListener("close", () => {
    if (changed) window.location.reload();
  });

  dialog.addEventListener("click", (event) => {
    // Clique no fundo escurecido (o próprio <dialog>) ou no botão de fechar.
    if (event.target === dialog || event.target.closest("[data-drawer-close]")) dialog.close();
  });
})();
