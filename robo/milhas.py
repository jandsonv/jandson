"""Consulta pontual de milhas: abre o site do programa, no navegador, só na
rota e data que o radar já apontou como baratas em dinheiro.

Sem login e sem burlar verificação: se o site barrar ou pedir captcha, o
resultado volta só com o link pronto para abrir manualmente.
"""
from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone

from .config import PERFIL, SAIDA

NOMES = {"smiles": "Smiles", "latam_pass": "LATAM Pass", "azul": "Azul Fidelidade"}

_RE_MILHAS = re.compile(r"(\d{1,3}(?:\.\d{3})+|\d{4,7})\s*(?:milhas|pontos|pts)\b", re.I)
_RE_BLOQUEIO = re.compile(
    r"captcha|n[ãa]o sou um rob[ôo]|verify you are human|access denied|acesso negado|"
    r"unusual traffic|pardon our interruption|press & hold",
    re.I,
)
_RE_SEM_VOO = re.compile(
    r"n[ãa]o encontramos|nenhum voo|n[ãa]o h[áa] voos|sem voos dispon|indispon[íi]vel para a data",
    re.I,
)


@dataclass
class ResultadoMilhas:
    programa: str
    link: str
    status: str = "nao_consultado"  # ok | sem_voo | sem_leitura | bloqueado | erro | nao_consultado
    milhas: int | None = None
    foto: str | None = None

    @property
    def nome(self) -> str:
        return NOMES.get(self.programa, self.programa)


def _ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, 15, tzinfo=timezone.utc).timestamp() * 1000)


def montar_link(modelo: str, origem: str, destino: str, ida: date, volta: date | None = None) -> str:
    campos = dict(
        origem=origem,
        destino=destino,
        ida=ida.isoformat(),
        ida_ms=_ms(ida),
        ida_mdy=ida.strftime("%m/%d/%Y"),
        ida_dmy=ida.strftime("%d-%m-%Y"),
        volta=volta.isoformat() if volta else "",
        volta_ms=_ms(volta) if volta else "",
        volta_mdy=volta.strftime("%m/%d/%Y") if volta else "",
    )
    return modelo.format(**campos)


def links_para(cfg_milhas: dict, origem: str, destino: str, ida: date, volta: date | None) -> list[ResultadoMilhas]:
    """Um resultado (ainda não consultado) por programa, com o link da busca."""
    saida = []
    for prog in cfg_milhas.get("programas", []):
        modelos = (cfg_milhas.get("links") or {}).get(prog) or {}
        modelo = modelos.get("ida_e_volta" if volta else "so_ida") or modelos.get("so_ida")
        if modelo:
            saida.append(ResultadoMilhas(prog, montar_link(modelo, origem, destino, ida, volta)))
    return saida


def extrair_milhas(texto: str) -> int | None:
    """Menor quantidade de milhas/pontos que aparece no texto da página de resultados."""
    valores = []
    for m in _RE_MILHAS.finditer(texto):
        v = int(m.group(1).replace(".", ""))
        if 1500 <= v <= 2_000_000:
            valores.append(v)
    return min(valores) if valores else None


def classificar(texto: str) -> tuple[str, int | None]:
    milhas = extrair_milhas(texto)
    if milhas:
        return "ok", milhas
    if _RE_BLOQUEIO.search(texto):
        return "bloqueado", None
    if _RE_SEM_VOO.search(texto):
        return "sem_voo", None
    return "sem_leitura", None


class Navegador:
    """Chrome do próprio computador, com um perfil separado só para o robô."""

    def __init__(self, cfg_milhas: dict, caminho_chromium: str | None = None):
        self.cfg = cfg_milhas
        self.caminho_chromium = caminho_chromium
        self._pw = None
        self._ctx = None
        self._consultas = 0

    def __enter__(self):
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        opcoes = dict(
            user_data_dir=str(PERFIL),
            headless=bool(self.cfg.get("navegador_oculto", False)),
            locale="pt-BR",
            timezone_id="America/Maceio",
            viewport={"width": 1366, "height": 850},
        )
        if self.caminho_chromium:
            self._ctx = self._pw.chromium.launch_persistent_context(
                executable_path=self.caminho_chromium, **opcoes
            )
        else:
            # Google Chrome; senão o Edge (vem no Windows); senão o Chromium do Playwright
            # (instalado por "python -m playwright install chromium").
            ultimo_erro = None
            for canal in ("chrome", "msedge", None):
                try:
                    extra = {"channel": canal} if canal else {}
                    self._ctx = self._pw.chromium.launch_persistent_context(**extra, **opcoes)
                    break
                except Exception as e:
                    ultimo_erro = e
            else:
                raise RuntimeError(
                    "Nenhum navegador encontrado. Instale o Google Chrome ou rode: "
                    "python -m playwright install chromium"
                ) from ultimo_erro
        return self

    def __exit__(self, *exc):
        try:
            if self._ctx:
                self._ctx.close()
        finally:
            if self._pw:
                self._pw.stop()

    def consultar(self, r: ResultadoMilhas, rotulo: str = "") -> ResultadoMilhas:
        if self._consultas:
            pausa = self.cfg.get("pausa_segundos", {})
            time.sleep(random.uniform(pausa.get("minimo", 25), pausa.get("maximo", 70)))
        self._consultas += 1
        espera = float(self.cfg.get("espera_resultado_segundos", 45))
        pagina = self._ctx.new_page()
        try:
            pagina.goto(r.link, wait_until="domcontentloaded", timeout=60_000)
            texto, fim = "", time.time() + espera
            while time.time() < fim:
                pagina.wait_for_timeout(3000)
                texto = pagina.inner_text("body", timeout=10_000)
                r.status, r.milhas = classificar(texto)
                if r.status in ("ok", "sem_voo", "bloqueado"):
                    break
            if r.status == "ok":
                # Dá tempo de a lista terminar de carregar e relê para pegar o menor valor.
                pagina.wait_for_timeout(4000)
                r.status, r.milhas = classificar(pagina.inner_text("body", timeout=10_000))
            SAIDA.mkdir(parents=True, exist_ok=True)
            base = SAIDA / re.sub(r"[^\w-]+", "_", f"{r.programa}_{rotulo}")
            pagina.screenshot(path=f"{base}.png")
            (base.with_suffix(".txt")).write_text(texto, encoding="utf-8")
            r.foto = f"{base}.png"
        except Exception as e:  # site fora do ar, tempo esgotado, layout diferente...
            r.status = "erro"
            print(f"  [milhas] {r.nome}: {type(e).__name__}: {str(e)[:150]}")
        finally:
            pagina.close()
        return r
