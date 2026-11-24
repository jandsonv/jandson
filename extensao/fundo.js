// Envia as leituras para o programa que roda no seu computador.
chrome.runtime.onMessage.addListener((msg, sender) => {
  fetch("http://localhost:8000/api/captura", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Radar-Extensao": "1" },
    body: JSON.stringify(msg),
  })
    .then(r => r.json())
    .then(r => {
      if (!sender.tab) return;
      chrome.action.setBadgeText({ tabId: sender.tab.id, text: r.lido ? "✓" : "" });
      chrome.action.setBadgeBackgroundColor({ tabId: sender.tab.id, color: "#0f7a4a" });
      chrome.action.setTitle({ tabId: sender.tab.id, title: r.mensagem || "Radar de Passagens" });
    })
    .catch(() => {});
});
