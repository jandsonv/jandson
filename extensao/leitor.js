// Lê o texto da página e os dados que ela carregou, e manda para o Radar de Passagens.
(() => {
  const jsons = [];
  let tamanho = 0;
  window.addEventListener("message", ev => {
    if (ev.source !== window || !ev.data || ev.data.radarPassagens !== "json") return;
    const t = JSON.stringify(ev.data.dados).length;
    if (tamanho + t > 4000000) return;
    jsons.push({ url: ev.data.url, dados: ev.data.dados });
    tamanho += t;
  });
  let ultimo = "";
  const enviar = () => {
    const texto = (document.body && document.body.innerText || "").slice(0, 300000);
    const assinatura = location.href + texto.length + ":" + jsons.length;
    if (assinatura === ultimo) return;
    ultimo = assinatura;
    chrome.runtime.sendMessage({ url: location.href, texto, json: jsons }).catch(() => {});
  };
  const inicio = Date.now();
  const timer = setInterval(() => {
    if (Date.now() - inicio > 10 * 60 * 1000) return clearInterval(timer);
    if (document.visibilityState === "visible") enviar();
  }, 3000);
})();
