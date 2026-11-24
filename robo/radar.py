"""Radar de preços em dinheiro usando a API de dados do Travelpayouts (Aviasales).

Os preços vêm de buscas recentes feitas por outras pessoas (cache), então
servem para apontar rota e data; o valor final é conferido no link.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import date, datetime

import requests

from .config import DADOS

API = "https://api.travelpayouts.com"


@dataclass
class Oferta:
    origem: str
    destino: str
    ida: date
    volta: date | None
    preco: float
    conexoes: int = 0
    cia: str = ""
    voo: str = ""
    hora_ida: str = ""
    link: str = ""
    internacional: bool = False
    nome_destino: str = ""
    motivo: str = ""
    milhas: list = field(default_factory=list)

    @property
    def tipo(self) -> str:
        return "ida_e_volta" if self.volta else "so_ida"

    @property
    def rota(self) -> str:
        return f"{self.origem}-{self.destino}"

    @property
    def chave(self) -> str:
        return f"voo:{self.rota}:{self.ida}:{self.volta or ''}"


class Cidades:
    """Nome e país de cada código IATA de cidade (base pública, guardada em disco por 30 dias)."""

    def __init__(self, sessao: requests.Session | None = None):
        self.sessao = sessao or requests.Session()
        self._mapa: dict[str, dict] | None = None

    def _carregar(self) -> dict[str, dict]:
        if self._mapa is not None:
            return self._mapa
        arq = DADOS / "cidades.json"
        if not arq.exists() or time.time() - arq.stat().st_mtime > 30 * 86400:
            try:
                r = self.sessao.get(f"{API}/data/pt/cities.json", timeout=60)
                r.raise_for_status()
                enxuto = {
                    c["code"]: {"nome": c.get("name") or c["code"], "pais": c.get("country_code", "")}
                    for c in r.json()
                    if c.get("code")
                }
                DADOS.mkdir(parents=True, exist_ok=True)
                arq.write_text(json.dumps(enxuto, ensure_ascii=False), encoding="utf-8")
            except (requests.RequestException, ValueError):
                if not arq.exists():
                    self._mapa = {}
                    return self._mapa
        self._mapa = json.loads(arq.read_text(encoding="utf-8"))
        return self._mapa

    def resolver(self, texto: str) -> str | None:
        """Aceita código IATA ('GRU') ou nome de cidade ('São Paulo') e devolve o código."""
        import unicodedata

        def norm(x: str) -> str:
            return unicodedata.normalize("NFKD", x).encode("ascii", "ignore").decode().lower().strip()

        t = texto.strip()
        mapa = self._carregar()
        if len(t) == 3 and t.isalpha() and (t.upper() in mapa or not mapa):
            return t.upper()
        alvo = norm(t)
        if not alvo:
            return None
        exatos = [c for c, v in mapa.items() if norm(v["nome"]) == alvo]
        if exatos:
            # Entre cidades com o mesmo nome, prefere a brasileira.
            return sorted(exatos, key=lambda c: mapa[c]["pais"] != "BR")[0]
        comeca = [c for c, v in mapa.items() if norm(v["nome"]).startswith(alvo)]
        return comeca[0] if len(comeca) == 1 else None

    def nome(self, codigo: str) -> str:
        return self._carregar().get(codigo, {}).get("nome", codigo)

    def internacional(self, codigo: str) -> bool:
        pais = self._carregar().get(codigo, {}).get("pais", "BR")
        return pais != "BR"


class Radar:
    def __init__(self, token: str, cfg: dict, sessao: requests.Session | None = None):
        self.cfg = cfg
        self.sessao = sessao or requests.Session()
        self.sessao.headers["X-Access-Token"] = token
        self.cidades = Cidades(requests.Session())

    def _get(self, caminho: str, **params) -> list[dict]:
        r = self.sessao.get(f"{API}{caminho}", params=params, timeout=40)
        if r.status_code == 401:
            raise SystemExit("Travelpayouts recusou o token (401). Confira TRAVELPAYOUTS_TOKEN no .env.")
        r.raise_for_status()
        corpo = r.json()
        if not corpo.get("success", True):
            raise RuntimeError(f"Travelpayouts: {corpo.get('error')}")
        dados = corpo.get("data") or []
        return dados if isinstance(dados, list) else list(dados.values())

    def _ultimos_precos(self, origem: str, so_ida: bool) -> list[dict]:
        return self._get(
            "/v2/prices/latest",
            origin=origem,
            currency="brl",
            period_type="year",
            one_way=str(so_ida).lower(),
            sorting="price",
            limit=1000,
            page=1,
        )

    def buscar(self, hoje: date | None = None) -> list[Oferta]:
        """Todas as ofertas dentro dos filtros básicos (sem julgar se o preço é bom)."""
        hoje = hoje or date.today()
        cfg = self.cfg
        tipo = cfg.get("tipo_viagem", "ambos")
        modos = [m for m, quer in ((True, tipo != "ida_e_volta"), (False, tipo != "so_ida")) if quer]
        ant = cfg.get("antecedencia_dias", {})
        dur = cfg.get("duracao_dias", {})
        ofertas: list[Oferta] = []
        for origem in cfg["origens"]:
            for so_ida in modos:
                for bruto in self._ultimos_precos(origem, so_ida):
                    o = self._converter(bruto, so_ida)
                    if o is None:
                        continue
                    if cfg["destinos"] and o.destino not in cfg["destinos"]:
                        continue
                    dias = (o.ida - hoje).days
                    if not ant.get("minimo", 0) <= dias <= ant.get("maximo", 365):
                        continue
                    if o.conexoes > cfg.get("max_conexoes", 2):
                        continue
                    if o.volta:
                        estadia = (o.volta - o.ida).days
                        if not dur.get("minimo", 0) <= estadia <= dur.get("maximo", 365):
                            continue
                    o.internacional = self.cidades.internacional(o.destino)
                    o.nome_destino = self.cidades.nome(o.destino)
                    ofertas.append(o)
        return ofertas

    @staticmethod
    def _converter(b: dict, so_ida: bool) -> Oferta | None:
        try:
            ida = date.fromisoformat(str(b["depart_date"])[:10])
            volta = None
            if not so_ida:
                volta = date.fromisoformat(str(b["return_date"])[:10])
            return Oferta(
                origem=b["origin"],
                destino=b["destination"],
                ida=ida,
                volta=volta,
                preco=float(b["value"]),
                conexoes=int(b.get("number_of_changes") or 0),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def _v3(self, origem: str, destino: str, quando: str, volta: str | None = None, limite: int = 30) -> list[dict]:
        params = dict(
            origin=origem,
            destination=destino,
            departure_at=quando,  # AAAA-MM-DD ou AAAA-MM (mês inteiro)
            currency="brl",
            one_way=str(volta is None).lower(),
            sorting="price",
            limit=limite,
        )
        if volta:
            params["return_at"] = volta
        return self._get("/aviasales/v3/prices_for_dates", **params)

    @staticmethod
    def _aplicar_v3(o: Oferta, m: dict) -> None:
        o.cia = str(m.get("airline") or "")
        o.voo = f"{o.cia}{m.get('flight_number')}" if m.get("flight_number") else ""
        try:
            o.hora_ida = datetime.fromisoformat(str(m["departure_at"])).strftime("%H:%M")
        except (KeyError, ValueError):
            pass
        if m.get("link"):
            o.link = "https://www.aviasales.com" + str(m["link"])

    def detalhar(self, o: Oferta) -> None:
        """Tenta descobrir companhia, número do voo e horário do mais barato naquela data."""
        try:
            achados = self._v3(o.origem, o.destino, o.ida.isoformat(), o.volta.isoformat() if o.volta else None, 5)
        except (requests.RequestException, RuntimeError, ValueError):
            return
        if achados:
            self._aplicar_v3(o, min(achados, key=lambda x: x.get("price", 1e12)))

    def melhor_do_mes(self, origem: str, destino: str, mes: str, hoje: date | None = None) -> Oferta | None:
        """Voo só de ida mais barato de `origem` para `destino` no mês AAAA-MM, com o dia exato."""
        hoje = hoje or date.today()
        melhor, bruto = None, None
        for m in self._v3(origem, destino, mes):
            try:
                ida = date.fromisoformat(str(m["departure_at"])[:10])
                preco = float(m["price"])
                conexoes = int(m.get("transfers") or 0)
            except (KeyError, TypeError, ValueError):
                continue
            if ida < hoje or ida.strftime("%Y-%m") != mes or conexoes > self.cfg.get("max_conexoes", 2):
                continue
            if melhor is None or preco < melhor.preco:
                melhor = Oferta(origem, destino, ida, None, preco, conexoes)
                bruto = m
        if melhor:
            self._aplicar_v3(melhor, bruto)
            melhor.internacional = self.cidades.internacional(destino)
            melhor.nome_destino = self.cidades.nome(destino)
        return melhor


def link_google_flights(o: Oferta) -> str:
    """Link de busca no Google Voos para conferir o preço em dinheiro."""
    from urllib.parse import quote

    q = f"voos de {o.origem} para {o.destino} em {o.ida.isoformat()}"
    q += f" volta {o.volta.isoformat()}" if o.volta else " só ida"
    return "https://www.google.com/travel/flights?hl=pt-BR&curr=BRL&q=" + quote(q)


def selecionar(ofertas: list[Oferta], cfg: dict, estado) -> list[Oferta]:
    """Fica só com o que é 'preço bom': abaixo do teto ou bem abaixo do histórico da rota.

    Mantém a data mais barata de cada rota e tipo de viagem.
    """
    regras = cfg.get("preco_bom", {})
    queda = regras.get("queda_sobre_historico_pct")
    boas: dict[tuple, Oferta] = {}
    for o in ofertas:
        mediana = estado.mediana(o.rota, o.tipo)
        estado.registrar_preco(o.rota, o.tipo, o.preco)
        teto = (regras.get("por_destino") or {}).get(o.destino, {}).get(o.tipo)
        if teto is None:
            grupo = "internacional" if o.internacional else "nacional"
            teto = (regras.get(grupo) or {}).get(o.tipo)
        if teto is not None and o.preco <= teto:
            o.motivo = f"abaixo do teto de R$ {teto:,.0f}".replace(",", ".")
        elif queda and mediana and o.preco <= mediana * (1 - queda / 100):
            pct = round((1 - o.preco / mediana) * 100)
            o.motivo = f"{pct}% abaixo do que costuma custar (R$ {mediana:,.0f})".replace(",", ".")
        else:
            continue
        chave = (o.rota, o.tipo)
        if chave not in boas or o.preco < boas[chave].preco:
            boas[chave] = o
    novas = [
        o
        for o in boas.values()
        if estado.deve_enviar(o.chave, o.preco, cfg.get("realertar_queda_pct", 10))
    ]
    novas.sort(key=lambda o: o.preco / (4 if o.internacional else 1))
    return novas[: cfg.get("max_alertas_por_rodada", 8)]
