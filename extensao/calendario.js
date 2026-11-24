// Google Voos: quando a página foi aberta pelo Radar (botão ⚡), abre o calendário de datas
// e lê o menor preço de cada dia, de todas as companhias. Só lê; não compra nem altera nada.
(() => {
  if (!location.pathname.startsWith("/travel/flights")) return;
  const espera = ms => new Promise(r => setTimeout(r, ms));
  const RE_PRECO = /R\$\s?([\d.]+(?:,\d{2})?)/;

  function lerDias() {
    const dias = {};
    document.querySelectorAll("[data-iso]").forEach(el => {
      const iso = el.getAttribute("data-iso");
      if (!/^\d{4}-\d{2}-\d{2}$/.test(iso)) return;
      const m = (el.innerText || el.getAttribute("aria-label") || "").match(RE_PRECO);
      if (!m) return;
      const valor = parseFloat(m[1].replace(/\./g, "").replace(",", "."));
      if (valor > 0 && (!dias[iso] || valor < dias[iso])) dias[iso] = valor;
    });
    return Object.entries(dias).map(([iso, preco]) => ({ iso, preco }));
  }

  function campoDeData() {
    const seletores = [
      'input[aria-label*="Partida" i]', 'input[placeholder*="Partida" i]',
      'input[aria-label*="Departure" i]', 'input[placeholder*="Departure" i]',
    ];
    for (const s of seletores) { const el = document.querySelector(s); if (el && el.offsetParent !== null) return el; }
    return null;
  }

  async function rodar() {
    // Espera a página montar, abre o calendário e espera os preços aparecerem.
    for (let i = 0; i < 20 && !campoDeData(); i++) await espera(500);
    let dias = lerDias();
    if (!dias.length) {
      const campo = campoDeData();
      if (campo) { campo.click(); campo.focus(); }
      for (let i = 0; i < 16 && lerDias().length < 10; i++) await espera(500);
      await espera(1500);
      dias = lerDias();
    }
    chrome.runtime.sendMessage({
      url: location.href,
      texto: (document.body.innerText || "").slice(0, 50000),
      calendario: dias,
    }).catch(() => {});
  }

  chrome.runtime.sendMessage({ tipo: "souCalendario" })
    .then(sim => { if (sim) rodar(); })
    .catch(() => {});
})();
