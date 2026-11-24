"""Sites de promoção: avisa quando sai post que cita as cidades de origem."""
from __future__ import annotations

import html
import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import requests

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
_RE_TAG = re.compile(r"<(script|style)\b.*?</\1>|<[^>]+>", re.S | re.I)
_RE_BONUS = re.compile(r"b[oô]nus.*transfer|transfer.*b[oô]nus", re.I)
_PROGRAMAS = {"smiles": "smiles", "latam_pass": "latam", "azul": "azul"}


@dataclass
class Promo:
    titulo: str
    link: str
    motivo: str

    @property
    def chave(self) -> str:
        return f"promo:{self.link}"


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return s.lower()


def _texto(marcacao: str) -> str:
    return html.unescape(_RE_TAG.sub(" ", marcacao or ""))


def ler_feed(xml: bytes | str) -> list[dict]:
    raiz = ET.fromstring(xml)
    itens = []
    for it in raiz.iter("item"):
        itens.append(
            dict(
                titulo=(it.findtext("title") or "").strip(),
                link=(it.findtext("link") or "").strip(),
                resumo=_texto(it.findtext("description") or ""),
            )
        )
    return itens


def _cita(texto: str, cidades: list[str]) -> str | None:
    t = _norm(texto)
    for c in cidades:
        if re.search(rf"\b{re.escape(_norm(c))}\b", t):
            return c
    return None


def avaliar(item: dict, cfg: dict, programas: list[str], baixar_post=None) -> Promo | None:
    """Decide se um post interessa. `baixar_post(link) -> texto` é usado só quando o
    título parece promoção de passagem mas não cita a cidade."""
    p = cfg.get("promocoes", {})
    cidades = p.get("cidades", [])
    titulo, resumo = item["titulo"], item["resumo"]
    cidade = _cita(titulo + " " + resumo, cidades)
    if cidade:
        return Promo(titulo, item["link"], f"cita {cidade}")
    if p.get("bonus_transferencia") and _RE_BONUS.search(titulo):
        t = _norm(titulo)
        for prog in programas:
            if _PROGRAMAS.get(prog, prog) in t:
                return Promo(titulo, item["link"], "bônus de transferência")
    palavras = [_norm(w) for w in p.get("palavras_titulo", [])]
    if baixar_post and any(re.search(rf"\b{re.escape(w)}\b", _norm(titulo)) for w in palavras):
        cidade = _cita(baixar_post(item["link"]), cidades)
        if cidade:
            return Promo(titulo, item["link"], f"o post cita {cidade}")
    return None


def buscar(cfg: dict, estado, programas: list[str], sessao: requests.Session | None = None) -> list[Promo]:
    p = cfg.get("promocoes", {})
    if not p.get("ativo", True):
        return []
    sessao = sessao or requests.Session()
    sessao.headers["User-Agent"] = UA

    def baixar_post(link: str) -> str:
        try:
            r = sessao.get(link, timeout=30)
            r.raise_for_status()
            corpo = r.text
            # Só o miolo do post, para não casar com menus e posts relacionados.
            m = re.search(r"<article\b.*?</article>", corpo, re.S | re.I)
            return _texto(m.group(0) if m else corpo)
        except requests.RequestException:
            return ""

    achados = []
    for url in p.get("feeds", []):
        try:
            r = sessao.get(url, timeout=30)
            r.raise_for_status()
            itens = ler_feed(r.content)
        except (requests.RequestException, ET.ParseError) as e:
            print(f"  [promoções] não consegui ler {url}: {type(e).__name__}")
            continue
        for item in itens:
            chave = f"promo:{item['link']}"
            visto = f"visto:{item['link']}"
            if not item["link"] or not estado.deve_enviar(chave) or not estado.deve_enviar(visto):
                continue
            promo = avaliar(item, cfg, programas, baixar_post)
            estado.marcar_enviado(visto)  # não reabre o mesmo post nas próximas rodadas
            if promo:
                achados.append(promo)
    return achados
