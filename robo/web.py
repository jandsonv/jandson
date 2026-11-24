"""Programinha web: login, busca por origem/destino e melhor preço por mês."""
from __future__ import annotations

import json
import queue
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
from .radar import Radar, link_google_flights

MAX_MESES = 12
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
    return db


def criar_usuario(caminho, nome: str, senha: str) -> None:
    if len(senha) < 8:
        raise ValueError("A senha precisa ter pelo menos 8 caracteres.")
    with closing(conectar(caminho)) as db, db:
        db.execute("INSERT OR REPLACE INTO usuarios VALUES(?,?)", (nome.strip().lower(), generate_password_hash(senha)))


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

    def executar(self, busca_id: int) -> None:
        cfg = self.cfg
        with closing(conectar(self.caminho_db)) as db:
            b = db.execute("SELECT * FROM buscas WHERE id=?", (busca_id,)).fetchone()
        origens, meses, destino = json.loads(b["origens"]), json.loads(b["meses"]), b["destino"]
        radar = self.fabrica_radar()
        if not radar.sessao.headers.get("X-Access-Token"):
            raise RuntimeError("Falta o TRAVELPAYOUTS_TOKEN no arquivo .env.")
        cias = cfg.get("companhias") or {}

        # 1) Dinheiro: melhor dia de cada mês
        achados = []  # (id do resultado, Oferta)
        total = len(origens) * len(meses)
        for n, (origem, mes) in enumerate(((o, m) for o in origens for m in meses), 1):
            self._marcar(busca_id, "rodando", f"Preços em dinheiro: {n} de {total} ({origem}, {mes})")
            try:
                o = radar.melhor_do_mes(origem, destino, mes)
            except Exception as e:
                print(f"[app] {origem}-{destino} {mes}: {type(e).__name__}: {str(e)[:150]}")
                o = None
            if not o:
                continue
            info = cias.get(o.cia) or {}
            link_cia = ""
            if info.get("link"):
                link_cia = mod_milhas.montar_link(info["link"], origem, destino, o.ida)
            with closing(conectar(self.caminho_db)) as db, db:
                cur = db.execute(
                    "INSERT INTO resultados(busca_id,origem,mes,ida,preco,cia,nome_cia,voo,hora,conexoes,"
                    "link_cia,link_google,milhas) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (busca_id, origem, mes, o.ida.isoformat(), o.preco, o.cia, info.get("nome") or o.cia,
                     o.voo, o.hora_ida, o.conexoes, link_cia, link_google_flights(o), "[]"),
                )
                achados.append((cur.lastrowid, o))
            time.sleep(0.3)

        # 2) Milhas: só no melhor dia de cada mês
        cfg_m = cfg.get("milhas", {})
        if b["com_milhas"] and achados:
            valores = cfg_m.get("valor_milheiro", {})
            pendentes = [(rid, o, mod_milhas.links_para(cfg_m, o.origem, o.destino, o.ida, None)) for rid, o in achados]
            total = sum(len(rs) for _, _, rs in pendentes)
            n = 0

            def gravar(rid, rs):
                dados = [
                    dict(programa=r.programa, nome=r.nome, status=r.status, milhas=r.milhas, link=r.link,
                         reais=round(r.milhas / 1000 * float(valores.get(r.programa, 0))) if r.milhas else None)
                    for r in rs
                ]
                with closing(conectar(self.caminho_db)) as db, db:
                    db.execute("UPDATE resultados SET milhas=? WHERE id=?", (json.dumps(dados), rid))

            for rid, _, rs in pendentes:
                gravar(rid, rs)  # os links já aparecem enquanto a leitura não chega
            try:
                with mod_milhas.Navegador(cfg_m, self.chromium) as nav:
                    for rid, o, rs in pendentes:
                        for r in rs:
                            n += 1
                            self._marcar(busca_id, "rodando", f"Milhas: {n} de {total} ({r.nome}, {o.origem}, {o.ida:%d/%m/%Y})")
                            nav.consultar(r, f"{o.rota}_{o.ida}")
                            gravar(rid, rs)
            except Exception as e:
                self._marcar(busca_id, "pronto", f"Não consegui abrir o navegador para as milhas ({type(e).__name__}). Os links seguem abaixo.")
                return
        self._marcar(busca_id, "pronto", "" if achados else "Nenhum preço encontrado para essa rota nesses meses.")


def criar_app(cfg: dict, caminho_db=None, chromium: str | None = None, fabrica_radar=None) -> Flask:
    caminho_db = caminho_db or DADOS / "app.db"
    app = Flask(__name__)
    app.secret_key = _chave_sessao(Path(caminho_db).parent)
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")
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
        if request.method == "POST" and (not esperado or request.form.get("csrf") != esperado):
            abort(400, "Sessão expirada. Volte e tente de novo.")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        erro = None
        if request.method == "POST":
            nome = request.form.get("usuario", "").strip().lower()
            with closing(conectar(caminho_db)) as db:
                u = db.execute("SELECT senha_hash FROM usuarios WHERE nome=?", (nome,)).fetchone()
            if u and check_password_hash(u["senha_hash"], request.form.get("senha", "")):
                session.clear()
                session["usuario"] = nome
                session.permanent = True
                return redirect(url_for("inicio"))
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
        form = form or {"inicio": _mes_padrao(1), "fim": _mes_padrao(6), "destino": "", "origens": cfg["origens"], "milhas": True}
        return render_template(
            "inicio.html", erro=erro, form=form, buscas=buscas, max_meses=MAX_MESES,
            origens=[(c, cidades.nome(c)) for c in cfg["origens"]],
            milhas_ativo=cfg.get("milhas", {}).get("ativo", True), json=json,
        )

    @app.post("/buscar")
    @logado
    def buscar():
        f = request.form
        form = {"inicio": f.get("inicio", ""), "fim": f.get("fim", ""), "destino": f.get("destino", "").strip(),
                "origens": [o for o in f.getlist("origens") if o in cfg["origens"]], "milhas": bool(f.get("milhas"))}
        try:
            meses = lista_meses(form["inicio"], form["fim"])
        except ValueError:
            meses = []
        destino = cidades.resolver(form["destino"]) if form["destino"] else None
        if not form["origens"]:
            return inicio("Escolha pelo menos uma origem.", form)
        if not destino:
            return inicio("Não reconheci o destino. Use o nome da cidade ou o código do aeroporto (ex.: GRU).", form)
        if destino in form["origens"]:
            return inicio("O destino é igual a uma das origens.", form)
        if not meses:
            return inicio("Confira o período: o mês final precisa ser igual ou depois do inicial.", form)
        with closing(conectar(caminho_db)) as db, db:
            cur = db.execute(
                "INSERT INTO buscas(usuario,origens,destino,nome_destino,meses,com_milhas,status,progresso,criado_em) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (session["usuario"], json.dumps(form["origens"]), destino, cidades.nome(destino), json.dumps(meses),
                 int(form["milhas"] and cfg.get("milhas", {}).get("ativo", True)), "na_fila", "Na fila…", time.time()),
            )
        executor.enfileirar(cur.lastrowid)
        return redirect(url_for("ver_busca", busca_id=cur.lastrowid))

    def _busca(busca_id: int):
        with closing(conectar(caminho_db)) as db:
            b = db.execute("SELECT * FROM buscas WHERE id=? AND usuario=?", (busca_id, session["usuario"])).fetchone()
            if not b:
                abort(404)
            rs = db.execute("SELECT * FROM resultados WHERE busca_id=? ORDER BY mes, preco", (busca_id,)).fetchall()
        return b, rs

    @app.route("/busca/<int:busca_id>")
    @logado
    def ver_busca(busca_id):
        b, _ = _busca(busca_id)
        return render_template("busca.html", b=b, origens=json.loads(b["origens"]), meses=json.loads(b["meses"]))

    @app.route("/api/busca/<int:busca_id>")
    @logado
    def api_busca(busca_id):
        b, rs = _busca(busca_id)
        return jsonify(
            status=b["status"], progresso=b["progresso"], meses=json.loads(b["meses"]),
            nomes={c: cidades.nome(c) for c in json.loads(b["origens"])},
            resultados=[{**{k: r[k] for k in r.keys() if k not in ("milhas", "busca_id")},
                         "milhas": json.loads(r["milhas"] or "[]")} for r in rs],
        )

    return app
