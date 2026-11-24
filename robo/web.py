"""Programinha web: login, busca por origem/destino e melhor preço por mês."""
from __future__ import annotations

import json
import queue
import random
import secrets
import sqlite3
import threading
import time
from contextlib import closing
from datetime import date
from functools import wraps
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from . import milhas as mod_milhas
from .config import DADOS, segredo
from .radar import Oferta, Radar, link_google_flights

MAX_MESES = 12
MSG_ASSISTIDO = ("Radar pronto. Para o preço real, clique em \"Site da…\" e nos programas de milhas: "
                 "a página abre no seu Chrome e a extensão lê o valor para esta tabela.")
SITES = {"smiles.com.br": "smiles", "latamairlines.com": "latam", "voeazul.com.br": "azul", "voegol.com.br": "gol",
         "google.com": "google"}


def chave_site(url: str) -> str | None:
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    for dominio, chave in SITES.items():
        if host == dominio or host.endswith("." + dominio):
            return chave
    return None
ESQUEMA = """
CREATE TABLE IF NOT EXISTS usuarios(nome TEXT PRIMARY KEY, senha_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS buscas(
    id INTEGER PRIMARY KEY AUTOINCREMENT, usuario TEXT, origens TEXT, destino TEXT,
    nome_destino TEXT, meses TEXT, com_milhas INTEGER, status TEXT, progresso TEXT, criado_em REAL);
CREATE TABLE IF NOT EXISTS resultados(
    id INTEGER PRIMARY KEY AUTOINCREMENT, busca_id INTEGER, origem TEXT, mes TEXT,
    ida TEXT, preco REAL, cia TEXT, nome_cia TEXT, voo TEXT, hora TEXT, conexoes INTEGER,
    link_cia TEXT, link_google TEXT, milhas TEXT);
"""


def conectar(caminho) -> sqlite3.Connection:
    Path(caminho).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(caminho), timeout=30)
    db.row_factory = sqlite3.Row
    db.executescript(ESQUEMA)
    # Atualiza bancos criados por versões anteriores.
    for tabela, coluna, tipo in (("buscas", "ida_volta", "INTEGER DEFAULT 0"), ("resultados", "trecho", "TEXT"),
                                 ("resultados", "de", "TEXT"), ("resultados", "para", "TEXT"),
                                 ("resultados", "preco_radar", "REAL"), ("usuarios", "chave", "TEXT"), ("resultados", "calendario", "TEXT"), ("buscas", "varredura", "INTEGER DEFAULT 0"), ("resultados", "conferido", "TEXT")):
        if coluna not in {c[1] for c in db.execute(f"PRAGMA table_info({tabela})")}:
            db.execute(f"ALTER TABLE {tabela} ADD COLUMN {coluna} {tipo}")
    return db


def criar_usuario(caminho, nome: str, senha: str) -> None:
    if len(senha) < 8:
        raise ValueError("A senha precisa ter pelo menos 8 caracteres.")
    with closing(conectar(caminho)) as db, db:
        nome = nome.strip().lower()
        if db.execute("SELECT 1 FROM usuarios WHERE nome=?", (nome,)).fetchone():
            db.execute("UPDATE usuarios SET senha_hash=? WHERE nome=?", (generate_password_hash(senha), nome))
        else:
            db.execute("INSERT INTO usuarios(nome, senha_hash) VALUES(?,?)", (nome, generate_password_hash(senha)))


def chave_do_usuario(caminho, nome: str) -> str:
    """Chave secreta que a extensão usa para mandar leituras para a conta deste usuário."""
    with closing(conectar(caminho)) as db, db:
        linha = db.execute("SELECT chave FROM usuarios WHERE nome=?", (nome,)).fetchone()
        if linha and linha["chave"]:
            return linha["chave"]
        chave = secrets.token_urlsafe(24)
        db.execute("UPDATE usuarios SET chave=? WHERE nome=?", (chave, nome))
        return chave


def lista_meses(inicio: str, fim: str) -> list[str]:
    """Meses AAAA-MM de `inicio` a `fim`, limitados a MAX_MESES."""
    a, m = map(int, inicio.split("-"))
    af, mf = map(int, fim.split("-"))
    if not (1 <= m <= 12 and 1 <= mf <= 12):
        raise ValueError("mês inválido")
    meses = []
    while (a, m) <= (af, mf) and len(meses) < MAX_MESES:
        meses.append(f"{a:04d}-{m:02d}")
        a, m = (a + 1, 1) if m == 12 else (a, m + 1)
    return meses


def _chave_sessao(pasta: Path) -> str:
    arq = Path(pasta) / "chave_sessao.txt"
    if not arq.exists():
        arq.parent.mkdir(parents=True, exist_ok=True)
        arq.write_text(secrets.token_hex(32), encoding="utf-8")
    return arq.read_text(encoding="utf-8").strip()


class Executor:
    """Faz as buscas uma de cada vez, em segundo plano."""

    def __init__(self, cfg: dict, caminho_db, chromium: str | None = None, fabrica_radar=None):
        self.cfg, self.caminho_db, self.chromium = cfg, caminho_db, chromium
        self.fabrica_radar = fabrica_radar or (lambda: Radar(segredo("TRAVELPAYOUTS_TOKEN", False), cfg))
        self.fila: queue.Queue[int] = queue.Queue()
        self._thread: threading.Thread | None = None

    def enfileirar(self, busca_id: int) -> None:
        self.fila.put(busca_id)
        if not self._thread or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._laco, daemon=True)
            self._thread.start()

    def _laco(self) -> None:
        while True:
            try:
                busca_id = self.fila.get(timeout=5)
            except queue.Empty:
                return
            try:
                self.executar(busca_id)
            except BaseException as e:  # inclui SystemExit de token recusado
                self._marcar(busca_id, "erro", str(e)[:300] or type(e).__name__)

    def _marcar(self, busca_id: int, status: str, progresso: str) -> None:
        with closing(conectar(self.caminho_db)) as db, db:
            db.execute("UPDATE buscas SET status=?, progresso=? WHERE id=?", (status, progresso, busca_id))

    def _varrer(self, busca_id, nav, meses, trechos, cias, fila, cfg_m, gravar) -> None:
        """Lê os preços de todos os dias de cada mês nos sites das companhias e fica com o melhor dia."""
        hoje = date.today()
        com_link = [(c, i) for c, i in cias.items() if i.get("link")]
        tarefas = [(mes, t, c, i) for mes in meses for t in trechos for c, i in com_link]
        por_rid = {rid: (o, rs) for _, rid, o, rs in fila}
        for k, (mes, (trecho, casa, de, para), cia, info) in enumerate(tarefas, 1):
            ano, m = map(int, mes.split("-"))
            dias: dict[date, float] = {}
            for ancora in (4, 11, 18, 25):
                dia = date(ano, m, ancora)
                if dia < hoje:
                    continue
                self._marcar(busca_id, "rodando", f"Varredura: {k} de {len(tarefas)} — {info.get('nome', cia)}, "
                                                  f"{de} → {para}, semana de {dia:%d/%m/%Y}")
                lidos = nav.ler_site_cia(mod_milhas.montar_link(info["link"], de, para, dia), dia,
                                         f"varredura_{cia}_{de}-{para}_{dia}")
                dias.update({d: v for d, v in lidos.items()
                             if d.strftime("%Y-%m") == mes and d >= hoje and v < dias.get(d, 1e12)})
                time.sleep(random.uniform(3, 8))
            if not dias:
                continue
            melhor = min(dias, key=dias.get)
            preco = dias[melhor]
            # Compara com o que já está na tabela para esse mês, trecho e cidade.
            with closing(conectar(self.caminho_db)) as db, db:
                atual = db.execute("SELECT id, preco, conferido FROM resultados WHERE busca_id=? AND mes=? AND trecho=? AND origem=?",
                                   (busca_id, mes, trecho, casa)).fetchone()
                # Preço lido ao vivo vale mais que o do cache; só mantém o atual se ele também foi lido ao vivo e é menor.
                if atual and atual["conferido"] and atual["preco"] <= preco:
                    continue
                o = Oferta(de, para, melhor, None, preco, cia=cia, nome_destino=para)
                nota = f"varredura no site da {info.get('nome', cia)}"
                valores = (mes, melhor.isoformat(), preco, cia, info.get("nome") or cia, "", "", 0,
                           mod_milhas.montar_link(info["link"], de, para, melhor), link_google_flights(o), nota)
                if atual:
                    db.execute("UPDATE resultados SET preco_radar=COALESCE(preco_radar, preco) WHERE id=?", (atual["id"],))
                    db.execute("UPDATE resultados SET mes=?, ida=?, preco=?, cia=?, nome_cia=?, voo=?, hora=?, conexoes=?, "
                               "link_cia=?, link_google=?, conferido=? WHERE id=?", valores + (atual["id"],))
                    rid = atual["id"]
                else:
                    rid = db.execute(
                        "INSERT INTO resultados(busca_id,origem,mes,ida,preco,cia,nome_cia,voo,hora,conexoes,link_cia,"
                        "link_google,conferido,trecho,de,para,milhas) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (busca_id, casa) + valores + (trecho, de, para, "[]")).lastrowid
            rs = mod_milhas.links_para(cfg_m, de, para, melhor, None)
            gravar(rid, rs)
            fila[:] = [x for x in fila if x[1] != rid] + [(preco, rid, o, rs)]
            por_rid[rid] = (o, rs)
        # Mantém só o mais barato de cada mês e trecho para a consulta de milhas.
        melhores = {}
        with closing(conectar(self.caminho_db)) as db:
            for r in db.execute("SELECT id, mes, trecho, preco FROM resultados WHERE busca_id=?", (busca_id,)):
                chave = (r["mes"], r["trecho"])
                if chave not in melhores or r["preco"] < melhores[chave][1]:
                    melhores[chave] = (r["id"], r["preco"])
        escolhidos = {rid for rid, _ in melhores.values()}
        fila[:] = sorted([x for x in fila if x[1] in escolhidos], key=lambda x: x[0])

    def executar(self, busca_id: int) -> None:
        cfg = self.cfg
        with closing(conectar(self.caminho_db)) as db:
            b = db.execute("SELECT * FROM buscas WHERE id=?", (busca_id,)).fetchone()
        origens, meses, destino = json.loads(b["origens"]), json.loads(b["meses"]), b["destino"]
        radar = self.fabrica_radar()
        if not radar.sessao.headers.get("X-Access-Token"):
            raise RuntimeError("Falta o TRAVELPAYOUTS_TOKEN no arquivo .env.")
        cias = cfg.get("companhias") or {}

        # Trechos: (trecho, cidade de casa, de, para)
        trechos = [("ida", o, o, destino) for o in origens]
        if b["ida_volta"]:
            trechos += [("volta", o, destino, o) for o in origens]

        # 1) Dinheiro: melhor dia de cada mês, em cada trecho
        achados = []  # (id do resultado, trecho, mês, Oferta)
        tarefas = [(mes, t) for mes in meses for t in trechos]
        for n, (mes, (trecho, casa, de, para)) in enumerate(tarefas, 1):
            self._marcar(busca_id, "rodando", f"Preços em dinheiro: {n} de {len(tarefas)} ({de} → {para}, {mes})")
            try:
                o = radar.melhor_do_mes(de, para, mes)
            except Exception as e:
                print(f"[app] {de}-{para} {mes}: {type(e).__name__}: {str(e)[:150]}")
                o = None
            if not o:
                continue
            info = cias.get(o.cia) or {}
            link_cia = mod_milhas.montar_link(info["link"], de, para, o.ida) if info.get("link") else ""
            with closing(conectar(self.caminho_db)) as db, db:
                cur = db.execute(
                    "INSERT INTO resultados(busca_id,origem,mes,ida,preco,cia,nome_cia,voo,hora,conexoes,"
                    "link_cia,link_google,milhas,trecho,de,para) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (busca_id, casa, mes, o.ida.isoformat(), o.preco, o.cia, info.get("nome") or o.cia,
                     o.voo, o.hora_ida, o.conexoes, link_cia, link_google_flights(o), "[]", trecho, de, para),
                )
                achados.append((cur.lastrowid, trecho, mes, o))
            time.sleep(0.3)

        # 2) Milhas: links para todos; consulta de verdade no mais barato de cada mês e trecho
        cfg_m = cfg.get("milhas", {})
        valores = cfg_m.get("valor_milheiro", {})

        def gravar(rid, rs):
            dados = [
                dict(programa=r.programa, nome=r.nome, status=r.status, milhas=r.milhas, link=r.link,
                     reais=round(r.milhas / 1000 * float(valores.get(r.programa, 0))) if r.milhas else None)
                for r in rs
            ]
            with closing(conectar(self.caminho_db)) as db, db:
                db.execute("UPDATE resultados SET milhas=? WHERE id=?", (json.dumps(dados), rid))

        if cfg.get("modo_sites", "assistido") != "robo":
            # Modo assistido: você abre os sites pelo programa e a extensão do Chrome lê os preços.
            for rid, _, _, o in achados:
                rs = mod_milhas.links_para(cfg_m, o.origem, o.destino, o.ida, None) if b["com_milhas"] else []
                for r in rs:
                    r.status = "abrir"
                gravar(rid, rs)
            self._marcar(busca_id, "pronto", MSG_ASSISTIDO if achados else
                         "Nenhum preço encontrado para essa rota nesses meses.")
            return

        conferir = cfg.get("conferir_no_site", True)
        if not b["varredura"] and not (achados and (b["com_milhas"] or conferir)):
            self._marcar(busca_id, "pronto", "" if achados else "Nenhum preço encontrado para essa rota nesses meses.")
            return
        mais_barato = {}
        for rid, trecho, mes, o in achados:
            if (mes, trecho) not in mais_barato or o.preco < mais_barato[(mes, trecho)][1].preco:
                mais_barato[(mes, trecho)] = (rid, o)
        escolhidos = {rid for rid, _ in mais_barato.values()}
        if cfg_m.get("consultar", "melhor_do_mes") == "todos":
            escolhidos = {rid for rid, *_ in achados}
        fila = []
        for rid, _, _, o in achados:
            rs = mod_milhas.links_para(cfg_m, o.origem, o.destino, o.ida, None)
            if rid not in escolhidos:
                for r in rs:
                    r.status = "so_link"
            gravar(rid, rs)  # os links aparecem logo, antes da leitura
            if rid in escolhidos:
                fila.append((o.preco, rid, o, rs))
        fila.sort(key=lambda x: x[0])  # as opções mais baratas são conferidas primeiro
        if not b["com_milhas"]:
            fila = [(p, rid, o, []) for p, rid, o, _ in fila]
        total = sum(len(rs) for *_, rs in fila)
        n, inicio = 0, time.time()
        try:
            with mod_milhas.Navegador(cfg_m, self.chromium) as nav:
                # 2a) Varredura completa: percorre o mês inteiro no site de cada companhia.
                if b["varredura"]:
                    self._varrer(busca_id, nav, meses, trechos, cias, fila, cfg_m, gravar)
                    if not b["com_milhas"]:
                        fila[:] = [(p, rid, o, []) for p, rid, o, _ in fila]
                # 2b) Preço real no site da companhia: confere o dia e os vizinhos do mesmo mês.
                if conferir and not b["varredura"]:
                    for k, (preco, rid, o, rs) in enumerate(fila, 1):
                        info = cias.get(o.cia) or {}
                        if not info.get("link"):
                            continue
                        self._marcar(busca_id, "rodando",
                                     f"Conferindo no site da {info.get('nome', o.cia)}: {k} de {len(fila)} "
                                     f"({o.origem} → {o.destino}, {o.ida:%m/%Y})")
                        dias = nav.ler_site_cia(mod_milhas.montar_link(info["link"], o.origem, o.destino, o.ida),
                                                o.ida, f"{o.rota}_{o.ida}")
                        dias = {d: v for d, v in dias.items() if d.strftime("%Y-%m") == o.ida.strftime("%Y-%m")
                                and d >= date.today()}
                        if not dias:
                            continue
                        melhor_dia = min(dias, key=dias.get)
                        nota = f"conferido no site da {info.get('nome', o.cia)}"
                        if melhor_dia != o.ida:
                            nota += f"; o radar indicava {o.ida:%d/%m} por R$ {o.preco:,.0f}".replace(",", ".")
                            o.ida, o.voo, o.hora_ida = melhor_dia, "", ""
                            o.link = ""
                            rs[:] = mod_milhas.links_para(cfg_m, o.origem, o.destino, o.ida, None) if rs else []
                        preco_radar, o.preco = o.preco, dias[melhor_dia]
                        with closing(conectar(self.caminho_db)) as db, db:
                            db.execute(
                                "UPDATE resultados SET ida=?, preco=?, preco_radar=?, conferido=?, voo=?, hora=?, "
                                "link_cia=?, link_google=? WHERE id=?",
                                (o.ida.isoformat(), o.preco, preco_radar, nota, o.voo, o.hora_ida,
                                 mod_milhas.montar_link(info["link"], o.origem, o.destino, o.ida),
                                 link_google_flights(o), rid),
                            )
                        if rs:
                            gravar(rid, rs)
                # 2c) Milhas no dia escolhido
                for _, rid, o, rs in fila:
                    for r in rs:
                        resta = ""
                        if n:
                            minutos = round((time.time() - inicio) / n * (total - n) / 60)
                            resta = f" · faltam cerca de {max(minutos, 1)} min"
                        n += 1
                        self._marcar(busca_id, "rodando",
                                     f"Milhas: {n} de {total} ({r.nome}, {o.origem} → {o.destino}, {o.ida:%d/%m/%Y}){resta}")
                        nav.consultar(r, f"{o.rota}_{o.ida}")
                        gravar(rid, rs)
        except Exception as e:
            self._marcar(busca_id, "pronto",
                         f"Não consegui abrir o navegador para as milhas ({type(e).__name__}). Os links seguem abaixo.")
            return
        self._marcar(busca_id, "pronto", "")


def criar_app(cfg: dict, caminho_db=None, chromium: str | None = None, fabrica_radar=None) -> Flask:
    caminho_db = caminho_db or DADOS / "app.db"
    app = Flask(__name__)
    app.secret_key = _chave_sessao(Path(caminho_db).parent)
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      MAX_CONTENT_LENGTH=8 * 1024 * 1024)

    @app.context_processor
    def _chave_extensao():
        # A extensão lê esta chave da página (meta "radar-chave") para saber para onde e em nome de quem enviar.
        u = session.get("usuario")
        return {"chave_extensao": chave_do_usuario(caminho_db, u) if u else ""}
    executor = Executor(cfg, caminho_db, chromium, fabrica_radar)
    app.executor = executor
    cidades = executor.fabrica_radar().cidades

    with closing(conectar(caminho_db)) as db, db:
        # Buscas interrompidas por um fechamento do programa não ficam "rodando" para sempre.
        db.execute("UPDATE buscas SET status='erro', progresso='Interrompida: o programa foi fechado.' "
                   "WHERE status IN ('na_fila','rodando')")

    def logado(f):
        @wraps(f)
        def dentro(*a, **kw):
            if not session.get("usuario"):
                if request.path.startswith("/api/"):
                    abort(401)
                return redirect(url_for("login"))
            return f(*a, **kw)
        return dentro

    def _csrf() -> str:
        if "csrf" not in session:
            session["csrf"] = secrets.token_hex(16)
        return session["csrf"]

    app.jinja_env.globals["csrf"] = _csrf

    @app.before_request
    def _confere_csrf():
        esperado = session.get("csrf")
        if request.path == "/api/captura":
            return None
        if request.method == "POST" and (not esperado or request.form.get("csrf") != esperado):
            abort(400, "Sessão expirada. Volte e tente de novo.")

    falhas: dict[str, list[float]] = {}
    trava_login = threading.Lock()

    @app.route("/login", methods=["GET", "POST"])
    def login():
        erro = None
        if request.method == "POST":
            ip = request.remote_addr or "?"
            agora = time.time()
            with trava_login:
                recentes = [t for t in falhas.get(ip, []) if agora - t < 15 * 60]
                falhas[ip] = recentes
            if len(recentes) >= 10:
                return render_template("login.html", erro="Muitas tentativas erradas. Espere 15 minutos."), 429
            nome = request.form.get("usuario", "").strip().lower()
            with closing(conectar(caminho_db)) as db:
                u = db.execute("SELECT senha_hash FROM usuarios WHERE nome=?", (nome,)).fetchone()
            if u and check_password_hash(u["senha_hash"], request.form.get("senha", "")):
                session.clear()
                session["usuario"] = nome
                session.permanent = True
                return redirect(url_for("inicio"))
            with trava_login:
                falhas.setdefault(ip, []).append(agora)
            time.sleep(1)  # atrasa tentativas de adivinhar a senha
            erro = "Usuário ou senha incorretos."
        return render_template("login.html", erro=erro)

    @app.post("/sair")
    def sair():
        session.clear()
        return redirect(url_for("login"))

    def _mes_padrao(desloca: int) -> str:
        h = date.today()
        total = h.year * 12 + h.month - 1 + desloca
        return f"{total // 12:04d}-{total % 12 + 1:02d}"

    @app.route("/")
    @logado
    def inicio(erro=None, form=None):
        with closing(conectar(caminho_db)) as db:
            buscas = db.execute(
                "SELECT b.*, (SELECT MIN(preco) FROM resultados r WHERE r.busca_id=b.id) AS menor "
                "FROM buscas b WHERE usuario=? ORDER BY id DESC LIMIT 15", (session["usuario"],)
            ).fetchall()
        form = form or {"inicio": _mes_padrao(1), "fim": _mes_padrao(6), "destino": "",
                        "origem": ", ".join(cidades.nome(c) for c in cfg["origens"][:1]),
                        "milhas": True, "ida_volta": True, "varredura": False}
        return render_template(
            "inicio.html", erro=erro, form=form, buscas=buscas, max_meses=MAX_MESES,
            origens=[(c, cidades.nome(c)) for c in cfg["origens"]],
            milhas_ativo=cfg.get("milhas", {}).get("ativo", True), json=json,
            modo_robo=cfg.get("modo_sites", "assistido") == "robo",
        )

    @app.post("/buscar")
    @logado
    def buscar():
        f = request.form
        form = {"inicio": f.get("inicio", ""), "fim": f.get("fim", ""), "destino": f.get("destino", "").strip(),
                "origem": f.get("origem", "").strip(), "milhas": bool(f.get("milhas")),
                "ida_volta": f.get("tipo") == "ida_volta",
                "varredura": bool(f.get("varredura")) and cfg.get("modo_sites", "assistido") == "robo"}
        try:
            meses = lista_meses(form["inicio"], form["fim"])
        except ValueError:
            meses = []
        destino = cidades.resolver(form["destino"]) if form["destino"] else None
        # Origem: uma cidade ou várias separadas por vírgula ("Aracaju, Salvador").
        partes = [x.strip() for x in form["origem"].split(",") if x.strip()]
        form["origens"] = [cidades.resolver(x) for x in partes]
        if not partes:
            return inicio("Diga de onde você sai.", form)
        if None in form["origens"]:
            ruim = partes[form["origens"].index(None)]
            return inicio(f"Não reconheci a origem \"{ruim}\". Use o nome da cidade ou o código do aeroporto (ex.: AJU).", form)
        form["origens"] = list(dict.fromkeys(form["origens"]))[:3]
        if not destino:
            return inicio("Não reconheci o destino. Use o nome da cidade ou o código do aeroporto (ex.: GRU).", form)
        if destino in form["origens"]:
            return inicio("O destino é igual a uma das origens.", form)
        if not meses:
            return inicio("Confira o período: o mês final precisa ser igual ou depois do inicial.", form)
        with closing(conectar(caminho_db)) as db, db:
            cur = db.execute(
                "INSERT INTO buscas(usuario,origens,destino,nome_destino,meses,com_milhas,status,progresso,criado_em,"
                "ida_volta,varredura) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (session["usuario"], json.dumps(form["origens"]), destino, cidades.nome(destino), json.dumps(meses),
                 int(form["milhas"] and cfg.get("milhas", {}).get("ativo", True)), "na_fila", "Na fila…", time.time(), int(form["ida_volta"]),
                 int(form["varredura"])),
            )
        executor.enfileirar(cur.lastrowid)
        return redirect(url_for("ver_busca", busca_id=cur.lastrowid))

    def _busca(busca_id: int):
        with closing(conectar(caminho_db)) as db:
            b = db.execute("SELECT * FROM buscas WHERE id=? AND usuario=?", (busca_id, session["usuario"])).fetchone()
            if not b:
                abort(404)
            rs = db.execute("SELECT * FROM resultados WHERE busca_id=? ORDER BY mes, trecho, preco", (busca_id,)).fetchall()
        return b, rs

    @app.route("/busca/<int:busca_id>")
    @logado
    def ver_busca(busca_id):
        b, _ = _busca(busca_id)
        return render_template("busca.html", b=b, origens=json.loads(b["origens"]), meses=json.loads(b["meses"]))

    aguardando: dict[tuple, dict] = {}  # (usuário, site) -> o que ele abriu por último nesse site
    trava = threading.Lock()

    def _linha(rid: int):
        with closing(conectar(caminho_db)) as db:
            return db.execute("SELECT r.*, b.usuario FROM resultados r JOIN buscas b ON b.id=r.busca_id WHERE r.id=?",
                              (rid,)).fetchone()

    @app.route("/abrir/<int:rid>/<alvo>")
    @logado
    def abrir(rid, alvo):
        r = _linha(rid)
        if not r or r["usuario"] != session["usuario"]:
            abort(404)
        if alvo == "cia":
            destino = r["link_cia"]
        elif alvo == "google":
            destino = r["link_google"]
        else:
            destino = next((m["link"] for m in json.loads(r["milhas"] or "[]") if m["programa"] == alvo), None)
        if not destino:
            abort(404)
        site = chave_site(destino)
        if site:
            with trava:
                aguardando[(session["usuario"], site)] = {"rid": rid, "alvo": alvo, "quando": time.time()}
        return redirect(destino)

    @app.post("/api/captura")
    def captura():
        # Só aceita leituras da extensão de um usuário, identificada pela chave secreta dele.
        chave = request.headers.get("X-Radar-Chave", "")
        with closing(conectar(caminho_db)) as db:
            u = db.execute("SELECT nome FROM usuarios WHERE chave=?", (chave,)).fetchone() if chave else None
        if not u:
            abort(403)
        dados = request.get_json(silent=True) or {}
        site = chave_site(str(dados.get("url", "")))
        with trava:
            alvo = aguardando.get((u["nome"], site)) if site else None
        if not alvo or time.time() - alvo["quando"] > 20 * 60:
            return jsonify(lido=False, mensagem="Abra esta página pelo Radar de Passagens para ela ser lida.")
        r = _linha(alvo["rid"])
        if not r or r["usuario"] != u["nome"]:
            return jsonify(lido=False, mensagem="Busca não encontrada.")
        texto, jsons = str(dados.get("texto", "")), [j.get("dados") for j in (dados.get("json") or [])
                                                      if isinstance(j, dict)]
        if alvo["alvo"] == "google":
            resultado = _ler_google(r, dados.get("calendario"))
            if not resultado["lido"] and dados.get("calendario") is not None:
                _guardar_diagnostico("google", dados)
            return jsonify(**resultado)
        if alvo["alvo"] == "cia":
            return jsonify(**_ler_cia(r, texto))
        return jsonify(**_ler_milhas(r, alvo["alvo"], texto, jsons))

    def _guardar_diagnostico(nome: str, dados: dict) -> None:
        """Guarda as últimas leituras que falharam, para calibrar a leitura daquele site."""
        try:
            pasta = Path(caminho_db).parent / "diagnostico"
            pasta.mkdir(parents=True, exist_ok=True)
            (pasta / f"{nome}-{int(time.time())}.json").write_text(json.dumps(dados, ensure_ascii=False)[:2_000_000],
                                                                    encoding="utf-8")
            for velho in sorted(pasta.glob(f"{nome}-*.json"))[:-5]:
                velho.unlink()
        except OSError:
            pass

    def _ler_google(r, calendario) -> dict:
        """Calendário do Google Voos: menor preço de cada dia, de todas as companhias."""
        piso = 0.5 * float(r["preco_radar"] or r["preco"])
        dias = {}
        for item in calendario or []:
            try:
                d, v = date.fromisoformat(str(item["iso"])), float(item["preco"])
            except (KeyError, TypeError, ValueError):
                continue
            if d.strftime("%Y-%m") == r["mes"] and d >= date.today() and v >= piso:
                dias[d] = min(v, dias.get(d, v))
        if not dias:
            return dict(lido=False, mensagem="Não encontrei os preços do calendário nesta página.")
        melhor = min(dias, key=dias.get)
        mudou_dia = melhor.isoformat() != r["ida"]
        nota = "calendário do Google Voos (todas as companhias)"
        if mudou_dia:
            nota += f"; o radar indicava {date.fromisoformat(r['ida']):%d/%m} por R$ {r['preco']:,.0f}".replace(",", ".")
        milhas = json.loads(r["milhas"] or "[]")
        if mudou_dia and milhas:
            novos = mod_milhas.links_para(cfg.get("milhas", {}), r["de"] or r["origem"], r["para"], melhor, None)
            milhas = [dict(programa=x.programa, nome=x.nome, status="abrir", milhas=None, link=x.link, reais=None)
                      for x in novos]
        o = Oferta(r["de"] or r["origem"], r["para"], melhor, None, dias[melhor])
        cal = json.dumps([{"iso": d.isoformat(), "preco": v} for d, v in sorted(dias.items())])
        with closing(conectar(caminho_db)) as db, db:
            if mudou_dia:
                # A companhia do dia novo não aparece no calendário: o link de compra passa a ser o Google Voos.
                db.execute("UPDATE resultados SET cia='', nome_cia='', voo='', hora='', conexoes=0, link_cia='' "
                           "WHERE id=?", (r["id"],))
            db.execute("UPDATE resultados SET ida=?, preco=?, preco_radar=COALESCE(preco_radar, ?), conferido=?, "
                       "link_google=?, milhas=?, calendario=? WHERE id=?",
                       (melhor.isoformat(), dias[melhor], r["preco"], nota, link_google_flights(o),
                        json.dumps(milhas), cal, r["id"]))
        return dict(lido=True, mensagem=f"Calendário lido: {len(dias)} dias; o mais barato é {melhor:%d/%m}")

    def _ler_cia(r, texto: str) -> dict:
        ref = date.fromisoformat(r["ida"])
        # Trava: uma tarifa menos da metade do que o radar viu é quase sempre outra coisa
        # (taxa, upgrade, bagagem), não a passagem.
        piso = 0.5 * float(r["preco_radar"] or r["preco"])
        dias = {d: v for d, v in mod_milhas.ler_calendario(texto, ref).items()
                if d.strftime("%Y-%m") == r["mes"] and d >= date.today() and v >= piso}
        if not dias:
            # Sites sem a faixa de dias vizinhos (ex.: LATAM): lê o menor preço da lista de voos do dia.
            preco = mod_milhas.menor_preco_pagina(texto)
            if preco is not None and preco < piso:
                preco = None
            if preco is None:
                return dict(lido=False, mensagem="Ainda não encontrei os preços nesta página.")
            dias = {ref: preco}
        melhor = min(dias, key=dias.get)
        info = (cfg.get("companhias") or {}).get(r["cia"]) or {}
        nome = info.get("nome") or r["nome_cia"] or r["cia"]
        nota = f"lido por você no site da {nome}"
        if melhor.isoformat() != r["ida"] and not (r["conferido"] or "").startswith("lido"):
            nota += f"; o radar indicava {ref:%d/%m} por R$ {r['preco']:,.0f}".replace(",", ".")
        elif melhor.isoformat() != r["ida"]:
            nota = (r["conferido"] or nota)
        mudou_dia = melhor.isoformat() != r["ida"]
        milhas = json.loads(r["milhas"] or "[]")
        if mudou_dia and milhas:
            novos = mod_milhas.links_para(cfg.get("milhas", {}), r["de"] or r["origem"], r["para"], melhor, None)
            milhas = [dict(programa=x.programa, nome=x.nome, status="abrir", milhas=None, link=x.link, reais=None)
                      for x in novos]
        o = Oferta(r["de"] or r["origem"], r["para"], melhor, None, dias[melhor])
        with closing(conectar(caminho_db)) as db, db:
            db.execute(
                "UPDATE resultados SET ida=?, preco=?, preco_radar=COALESCE(preco_radar, ?), conferido=?, "
                "voo=CASE WHEN ?=1 THEN '' ELSE voo END, hora=CASE WHEN ?=1 THEN '' ELSE hora END, "
                "link_cia=?, link_google=?, milhas=? WHERE id=?",
                (melhor.isoformat(), dias[melhor], r["preco"], nota, int(mudou_dia), int(mudou_dia),
                 mod_milhas.montar_link(info["link"], o.origem, o.destino, melhor) if info.get("link") else r["link_cia"],
                 link_google_flights(o), json.dumps(milhas), r["id"]),
            )
        return dict(lido=True, mensagem=f"Lido: R$ {dias[melhor]:,.2f} em {melhor:%d/%m}".replace(",", "X")
                    .replace(".", ",").replace("X", "."))

    def _ler_milhas(r, programa: str, texto: str, jsons: list) -> dict:
        status, valor = mod_milhas.classificar(texto, jsons)
        if status not in ("ok", "sem_voo", "bloqueado"):
            return dict(lido=False, mensagem="Ainda não encontrei as milhas nesta página.")
        valores = cfg.get("milhas", {}).get("valor_milheiro", {})
        milhas = json.loads(r["milhas"] or "[]")
        for m in milhas:
            if m["programa"] == programa:
                m.update(status=status, milhas=valor,
                         reais=round(valor / 1000 * float(valores.get(programa, 0))) if valor else None)
        with closing(conectar(caminho_db)) as db, db:
            db.execute("UPDATE resultados SET milhas=? WHERE id=?", (json.dumps(milhas), r["id"]))
        textos = {"ok": f"Lido: {valor:,} milhas".replace(",", "."), "sem_voo": "Sem voo em milhas nessa data",
                  "bloqueado": "O site pediu verificação"}
        return dict(lido=status == "ok", mensagem=textos[status])

    @app.route("/api/busca/<int:busca_id>")
    @logado
    def api_busca(busca_id):
        b, rs = _busca(busca_id)
        return jsonify(
            status=b["status"], progresso=b["progresso"], meses=json.loads(b["meses"]), ida_volta=bool(b["ida_volta"]),
            destino=b["destino"], nome_destino=b["nome_destino"],
            nomes={c: cidades.nome(c) for c in json.loads(b["origens"]) + [b["destino"]]},
            resultados=[{**{k: r[k] for k in r.keys() if k not in ("milhas", "busca_id")},
                         "milhas": json.loads(r["milhas"] or "[]")} for r in rs],
        )

    return app
