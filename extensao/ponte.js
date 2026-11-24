// Ponte entre a página do Radar de Passagens (localhost) e a extensão:
// avisa a página que a extensão está instalada e repassa os pedidos de conferência.
document.documentElement.dataset.radarExtensao = chrome.runtime.getManifest().version;
window.addEventListener("message", ev => {
  if (ev.source !== window || !ev.data || !Array.isArray(ev.data.radarConferir)) return;
  const urls = ev.data.radarConferir.filter(u => typeof u === "string" && u.startsWith(location.origin + "/abrir/"));
  chrome.runtime.sendMessage({ tipo: "conferir", urls }).catch(() => {});
});
chrome.runtime.onMessage.addListener(msg => {
  if (msg && msg.tipo === "progresso") window.postMessage({ radarProgresso: msg }, location.origin);
});
