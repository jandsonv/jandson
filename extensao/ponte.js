// Ponte entre a página do Radar de Passagens e a extensão:
// - ensina à extensão o endereço do programa e a chave da sua conta;
// - avisa a página que a extensão está instalada;
// - repassa os pedidos de conferência.
document.documentElement.dataset.radarExtensao = chrome.runtime.getManifest().version;
function enviarConfig() {
  const meta = document.querySelector('meta[name="radar-chave"]');
  if (meta && meta.content) chrome.runtime.sendMessage({ tipo: "config", servidor: location.origin, chave: meta.content }).catch(() => {});
}
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", enviarConfig); else enviarConfig();
window.addEventListener("message", ev => {
  if (ev.source !== window || !ev.data || !Array.isArray(ev.data.radarConferir)) return;
  const urls = ev.data.radarConferir.filter(u => typeof u === "string" && u.startsWith(location.origin + "/abrir/"));
  chrome.runtime.sendMessage({ tipo: "conferir", urls }).catch(() => {});
});
chrome.runtime.onMessage.addListener(msg => {
  if (msg && msg.tipo === "progresso") window.postMessage({ radarProgresso: msg }, location.origin);
});
