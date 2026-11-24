// Roda dentro da página: repassa ao leitor as respostas JSON que o próprio site carrega
// (é onde ficam os preços e as milhas). Não altera nada no site.
(() => {
  const LIMITE = 3000000;
  const avisar = (url, texto) => {
    if (!texto || texto.length > LIMITE) return;
    try { window.postMessage({ radarPassagens: "json", url: String(url), dados: JSON.parse(texto) }, "*"); } catch (e) {}
  };
  const fetchOriginal = window.fetch;
  window.fetch = async function (...args) {
    const resposta = await fetchOriginal.apply(this, args);
    try {
      if ((resposta.headers.get("content-type") || "").includes("json")) {
        resposta.clone().text().then(t => avisar(resposta.url, t)).catch(() => {});
      }
    } catch (e) {}
    return resposta;
  };
  const abrir = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (metodo, url, ...resto) {
    this.addEventListener("load", () => {
      try {
        if ((this.getResponseHeader("content-type") || "").includes("json") &&
            (this.responseType === "" || this.responseType === "text")) avisar(url, this.responseText);
      } catch (e) {}
    });
    return abrir.call(this, metodo, url, ...resto);
  };
})();
