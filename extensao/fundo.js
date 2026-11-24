// Envia as leituras para o programa no seu computador e, quando você pede,
// abre as páginas da conferência uma de cada vez, em segundo plano.
const fila = [];
let rodando = false;
let atual = null;
let abaDoPrograma = null;
const abasCalendario = new Set();
const espera = ms => new Promise(r => setTimeout(r, ms));

function avisarPrograma(dados) {
  if (abaDoPrograma != null) chrome.tabs.sendMessage(abaDoPrograma, { tipo: "progresso", ...dados }).catch(() => {});
}

async function processar() {
  if (rodando) return;
  rodando = true;
  let feitos = 0;
  while (fila.length) {
    const url = fila.shift();
    avisarPrograma({ feitos, total: feitos + fila.length + 1 });
    let aba;
    try {
      aba = await chrome.tabs.create({ url, active: false });
    } catch (e) { continue; }
    if (url.endsWith("/google")) abasCalendario.add(aba.id);
    await new Promise(resolve => {
      const limite = setTimeout(() => resolve(false), url.endsWith("/google") ? 30000 : 45000);
      atual = { tabId: aba.id, pronto: () => { clearTimeout(limite); setTimeout(() => resolve(true), 4000); } };
      // Se a aba não saiu do programa (nada para abrir nesse site), segue para a próxima.
      const vigia = (id, info) => {
        if (id !== aba.id || info.status !== "complete") return;
        chrome.tabs.get(id).then(t => {
          if (t.url && t.url.includes("/abrir/")) { clearTimeout(limite); resolve(false); }
        }).catch(() => {});
      };
      chrome.tabs.onUpdated.addListener(vigia);
      setTimeout(() => chrome.tabs.onUpdated.removeListener(vigia), 46000);
    });
    abasCalendario.delete(aba.id);
    atual = null;
    try { await chrome.tabs.remove(aba.id); } catch (e) {}
    feitos++;
    // Pausa entre páginas, no ritmo de uma pessoa.
    if (fila.length) await espera(5000 + Math.random() * 6000);
  }
  avisarPrograma({ feitos, total: feitos, fim: true });
  rodando = false;
}

chrome.runtime.onMessage.addListener((msg, sender, responder) => {
  if (msg && msg.tipo === "config") {
    // Endereço do programa (no seu Mac ou na internet) e a chave da sua conta.
    chrome.storage.local.set({ servidor: msg.servidor, chave: msg.chave });
    return;
  }
  if (msg && msg.tipo === "souCalendario") {
    // Só abre o calendário do Google nas abas que a própria extensão abriu pelo Radar.
    responder(!!(sender.tab && abasCalendario.has(sender.tab.id)));
    return;
  }
  if (msg && msg.tipo === "conferir") {
    if (sender.tab) abaDoPrograma = sender.tab.id;
    for (const u of msg.urls) if (!fila.includes(u)) fila.push(u);
    processar();
    return;
  }
  chrome.storage.local.get(["servidor", "chave"])
    .then(cfg => {
      if (!cfg.servidor || !cfg.chave) throw new Error("Abra o Radar de Passagens uma vez para conectar a extensão.");
      return fetch(cfg.servidor + "/api/captura", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Radar-Chave": cfg.chave },
        body: JSON.stringify(msg),
      });
    })
    .then(r => r.json())
    .then(r => {
      if (!sender.tab) return;
      chrome.action.setBadgeText({ tabId: sender.tab.id, text: r.lido ? "✓" : "" });
      chrome.action.setBadgeBackgroundColor({ tabId: sender.tab.id, color: "#0f7a4a" });
      chrome.action.setTitle({ tabId: sender.tab.id, title: r.mensagem || "Radar de Passagens" });
      if (r.lido && atual && atual.tabId === sender.tab.id) { const a = atual; atual = { tabId: -1 }; a.pronto(); }
    })
    .catch(() => {});
});
