"""Envio de alertas pelo Telegram e montagem das mensagens."""
from __future__ import annotations

from html import escape

import requests

from .radar import Oferta, link_google_flights

DIAS = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]


def _rs(v: float) -> str:
    return "R$ " + f"{v:,.0f}".replace(",", ".")


def _mil(v: int) -> str:
    return f"{v:,}".replace(",", ".")


def _data(d) -> str:
    return f"{d.strftime('%d/%m/%Y')} ({DIAS[d.weekday()]})"


def texto_oferta(o: Oferta, nome_origem: str, valor_milheiro: dict) -> str:
    seta = "⇄" if o.volta else "→"
    linhas = [
        f"✈️ <b>{escape(nome_origem)} ({o.origem}) {seta} {escape(o.nome_destino or o.destino)} ({o.destino})</b>",
        f"📅 Ida {_data(o.ida)}" + (f" · volta {_data(o.volta)}" if o.volta else " · só ida"),
        f"💵 <b>{_rs(o.preco)}</b> em dinheiro — {escape(o.motivo)}",
    ]
    detalhe = []
    if o.voo:
        detalhe.append(f"voo {o.voo}" + (f" às {o.hora_ida}" if o.hora_ida else ""))
    detalhe.append("direto" if o.conexoes == 0 else f"{o.conexoes} conexão(ões)")
    linhas.append("🛫 " + ", ".join(detalhe))
    links = [f'<a href="{escape(link_google_flights(o))}">Google Voos</a>']
    if o.link:
        links.append(f'<a href="{escape(o.link)}">Aviasales</a>')
    linhas.append("🔗 Conferir: " + " · ".join(links))

    if o.milhas:
        linhas.append("")
        linhas.append("🎯 <b>Milhas na mesma data</b>" + (" (trecho de ida)" if o.volta else ""))
        melhor = None
        for r in o.milhas:
            abrir = f'<a href="{escape(r.link)}">abrir busca</a>'
            if r.status == "ok" and r.milhas:
                reais = r.milhas / 1000 * float(valor_milheiro.get(r.programa, 0))
                eq = f" ≈ {_rs(reais)}" if reais else ""
                linhas.append(f"• {r.nome}: a partir de <b>{_mil(r.milhas)}</b> + taxas{eq} — {abrir}")
                if reais and (melhor is None or reais < melhor[0]):
                    melhor = (reais, r.nome)
            elif r.status == "sem_voo":
                linhas.append(f"• {r.nome}: sem voo em milhas nessa data — {abrir}")
            elif r.status == "bloqueado":
                linhas.append(f"• {r.nome}: o site pediu verificação — {abrir}")
            elif r.status == "nao_consultado":
                linhas.append(f"• {r.nome}: {abrir}")
            else:
                linhas.append(f"• {r.nome}: não consegui ler o resultado — {abrir}")
        if melhor and not o.volta:
            if melhor[0] < o.preco:
                linhas.append(f"👉 Compensa mais em milhas ({melhor[1]}), se as taxas forem baixas.")
            else:
                linhas.append("👉 Compensa mais em dinheiro.")
    return "\n".join(linhas)


def texto_promo(p) -> str:
    return f'📰 <b>{escape(p.titulo)}</b>\n({escape(p.motivo)})\n<a href="{escape(p.link)}">Abrir o post</a>'


class Telegram:
    def __init__(self, token: str, chat_id: str, sessao: requests.Session | None = None):
        self.base = f"https://api.telegram.org/bot{token}"
        self.chat_id = chat_id
        self.sessao = sessao or requests.Session()

    def _chamar(self, metodo: str, **kw) -> dict:
        r = self.sessao.post(f"{self.base}/{metodo}", timeout=60, **kw)
        corpo = r.json()
        if not corpo.get("ok"):
            raise RuntimeError(f"Telegram recusou ({metodo}): {corpo.get('description')}")
        return corpo

    def enviar(self, texto: str) -> None:
        self._chamar(
            "sendMessage",
            data=dict(chat_id=self.chat_id, text=texto, parse_mode="HTML", disable_web_page_preview="true"),
        )

    def enviar_foto(self, caminho: str, legenda: str = "") -> None:
        with open(caminho, "rb") as f:
            self._chamar("sendPhoto", data=dict(chat_id=self.chat_id, caption=legenda[:1000]), files={"photo": f})

    def conversas(self) -> list[tuple[str, str]]:
        """(chat_id, nome) de quem mandou mensagem para o bot recentemente."""
        achados = {}
        for u in self._chamar("getUpdates").get("result", []):
            chat = (u.get("message") or u.get("channel_post") or {}).get("chat")
            if chat:
                nome = chat.get("title") or chat.get("first_name") or chat.get("username") or ""
                achados[str(chat["id"])] = nome
        return list(achados.items())


class TelegramSimulado:
    """Imprime na tela em vez de enviar (opção --simular)."""

    def enviar(self, texto: str) -> None:
        print("\n----- mensagem -----\n" + texto + "\n--------------------")

    def enviar_foto(self, caminho: str, legenda: str = "") -> None:
        print(f"[foto] {caminho} — {legenda}")
