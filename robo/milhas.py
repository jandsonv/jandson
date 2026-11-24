"""Consulta pontual de milhas: abre o site do programa, no navegador, só na
rota e data que o radar já apontou como baratas em dinheiro.

Sem login e sem burlar verificação: se o site barrar ou pedir captcha, o
resultado volta só com o link pronto para abrir manualmente.
"""
from __future__ import annotations

import json
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


_CHAVE_MILHAS = re.compile(r"mile|milha|point|ponto|pts", re.I)
_CHAVE_IGNORAR = re.compile(
    r"balance|saldo|earn|accru|acumul|bonus|club|clube|tier|qualif|transfer|purchase|buy|compra|"
    r"accumul|elite|status|expir|venc|gift|reward",
    re.I,
)


def extrair_milhas_json(dado, _chave: str = "") -> list[int]:
    """Valores de milhas/pontos em dados JSON (as respostas que o site carrega por trás da página)."""
    achados: list[int] = []
    if isinstance(dado, dict):
        for k, v in dado.items():
            k = str(k)
            if isinstance(v, (int, float, str)) and not isinstance(v, bool):
                if _CHAVE_MILHAS.search(k) and not _CHAVE_IGNORAR.search(k):
                    try:
                        n = int(float(str(v).replace(".", "") if isinstance(v, str) else v))
                    except ValueError:
                        continue
                    if 1500 <= n <= 2_000_000:
                        achados.append(n)
            elif not _CHAVE_IGNORAR.search(k):
                achados += extrair_milhas_json(v, k)
    elif isinstance(dado, list):
        for v in dado:
            achados += extrair_milhas_json(v, _chave)
    return achados


_MESES_ABREV = {"jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6, "jul": 7, "ago": 8,
                "set": 9, "out": 10, "nov": 11, "dez": 12}
_RE_DIA_PRECO = re.compile(
    r"(?:dom|seg|ter|qua|qui|sex|s[áa]b)\w*\.?,?\s+(\d{1,2})\s+(?:de\s+)?"
    r"(jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)\w*\.?"
    r"(?:(?!(?:dom|seg|ter|qua|qui|sex|s[áa]b)\w*\.?,?\s+\d{1,2}\s).){0,80}?"
    r"(?:R\$|BRL)\s*(\d{1,3}(?:\.\d{3})*,\d{2})",
    re.I | re.S,
)


def ler_calendario(texto: str, referencia: date) -> dict[date, float]:
    """Lê a faixa de dias vizinhos com preço ('Sex, 25 Dez · A partir de BRL 390,52')."""
    dias: dict[date, float] = {}
    for m in _RE_DIA_PRECO.finditer(texto):
        dia, mes = int(m.group(1)), _MESES_ABREV[m.group(2).lower()[:3]]
        preco = float(m.group(3).replace(".", "").replace(",", "."))
        candidatos = []
        for ano in (referencia.year - 1, referencia.year, referencia.year + 1):
            try:
                candidatos.append(date(ano, mes, dia))
            except ValueError:
                pass
        if not candidatos:
            continue
        d = min(candidatos, key=lambda x: abs((x - referencia).days))
        if d not in dias or preco < dias[d]:
            dias[d] = preco
    return dias


_RE_PRECO = re.compile(r"(?:R\$|BRL)\s*(\d{1,3}(?:\.\d{3})*,\d{2})", re.I)


def menor_preco_pagina(texto: str) -> float | None:
    """Menor preço em reais na página de resultados de um dia (ex.: 'Por pessoa a partir de BRL 459,94')."""
    valores = [float(m.group(1).replace(".", "").replace(",", ".")) for m in _RE_PRECO.finditer(texto)]
    valores = [v for v in valores if v >= 50]
    return min(valores) if valores else None


def classificar(texto: str, dados_json: list | None = None) -> tuple[str, int | None]:
    valores = [n for d in (dados_json or []) for n in extrair_milhas_json(d)]
    if valores:
        return "ok", min(valores)
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
        self._abrir()
        return self

    def _nova_pagina(self):
        """Nova aba; se a janela do robô foi fechada, abre outra e segue."""
        try:
            return self._ctx.new_page()
        except Exception:
            print("  [navegador] a janela do robô foi fechada; abrindo de novo.")
            try:
                self._ctx.close()
            except Exception:
                pass
            self._abrir()
            return self._ctx.new_page()

    def _abrir(self) -> None:
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

    def __exit__(self, *exc):
        try:
            if self._ctx:
                self._ctx.close()
        finally:
            if self._pw:
                self._pw.stop()

    def abrir_para_login(self, links: list[str]) -> None:
        """Abre os sites em abas da janela do robô e espera você fechar a janela."""
        for link in links:
            self._ctx.new_page().goto(link, wait_until="domcontentloaded", timeout=60_000)
        for p in list(self._ctx.pages):
            if p.url == "about:blank" and len(self._ctx.pages) > 1:
                p.close()
        print("Entre nas suas contas nas abas abertas. Quando terminar, feche a janela do navegador.")
        while self._ctx.pages:
            try:
                self._ctx.pages[0].wait_for_timeout(1000)
            except Exception:
                break

    def ler_site_cia(self, link: str, referencia: date, rotulo: str = "") -> dict[date, float]:
        """Abre a busca no site da companhia e lê os preços do dia e dos dias vizinhos."""
        espera = float(self.cfg.get("espera_resultado_segundos", 45))
        pagina = self._nova_pagina()
        dias: dict[date, float] = {}
        texto = ""
        try:
            pagina.goto(link, wait_until="domcontentloaded", timeout=60_000)
            fim = time.time() + espera
            while time.time() < fim:
                pagina.wait_for_timeout(3000)
                texto = pagina.inner_text("body", timeout=10_000)
                dias = ler_calendario(texto, referencia)
                if referencia in dias:
                    pagina.wait_for_timeout(2000)
                    texto = pagina.inner_text("body", timeout=10_000)
                    dias = ler_calendario(texto, referencia) or dias
                    break
        except Exception as e:
            print(f"  [cia] {type(e).__name__}: {str(e)[:150]}")
        finally:
            try:
                SAIDA.mkdir(parents=True, exist_ok=True)
                base = SAIDA / re.sub(r"[^\w-]+", "_", f"cia_{rotulo}")
                pagina.screenshot(path=f"{base}.png")
                base.with_suffix(".txt").write_text(f"{pagina.url}\n\n{texto}", encoding="utf-8")
            except Exception:
                pass
            pagina.close()
        return dias

    def consultar(self, r: ResultadoMilhas, rotulo: str = "") -> ResultadoMilhas:
        if self._consultas:
            pausa = self.cfg.get("pausa_segundos", {})
            time.sleep(random.uniform(pausa.get("minimo", 12), pausa.get("maximo", 35)))
        self._consultas += 1
        espera = float(self.cfg.get("espera_resultado_segundos", 45))
        respostas: list[tuple[str, object]] = []

        def ao_receber(resp):
            try:
                tipo = resp.headers.get("content-type", "")
                if "json" not in tipo or resp.request.resource_type not in ("xhr", "fetch"):
                    return
                corpo = resp.body()
                if len(corpo) > 5_000_000:
                    return
                respostas.append((resp.url, json.loads(corpo)))
            except Exception:
                pass

        pagina = self._nova_pagina()
        pagina.on("response", ao_receber)
        texto = ""
        try:
            pagina.goto(r.link, wait_until="domcontentloaded", timeout=60_000)
            fim = time.time() + espera
            while time.time() < fim:
                pagina.wait_for_timeout(3000)
                texto = pagina.inner_text("body", timeout=10_000)
                r.status, r.milhas = classificar(texto, [d for _, d in respostas])
                if r.status in ("ok", "sem_voo", "bloqueado"):
                    break
            if r.status == "ok":
                # Dá tempo de a lista terminar de carregar e relê para pegar o menor valor.
                pagina.wait_for_timeout(4000)
                texto = pagina.inner_text("body", timeout=10_000)
                r.status, r.milhas = classificar(texto, [d for _, d in respostas])
        except Exception as e:  # site fora do ar, tempo esgotado, layout diferente...
            r.status = "erro"
            print(f"  [milhas] {r.nome}: {type(e).__name__}: {str(e)[:150]}")
        finally:
            # Guarda o que o robô viu, para conferir e calibrar a leitura.
            try:
                SAIDA.mkdir(parents=True, exist_ok=True)
                base = SAIDA / re.sub(r"[^\w-]+", "_", f"{r.programa}_{rotulo}")
                pagina.screenshot(path=f"{base}.png")
                r.foto = f"{base}.png"
                base.with_suffix(".txt").write_text(f"{pagina.url}\n\n{texto}", encoding="utf-8")
                with open(f"{base}.json", "w", encoding="utf-8") as f:
                    json.dump([{"url": u, "dados": d} for u, d in respostas], f, ensure_ascii=False)
            except Exception:
                pass
            pagina.close()
        return r
