from datetime import date

from robo import milhas, promos
from robo.estado import Estado
from robo.radar import Oferta, Radar, selecionar
from robo.telegram import texto_oferta

CFG = {
    "origens": ["AJU"], "destinos": None, "tipo_viagem": "ambos",
    "antecedencia_dias": {"minimo": 10, "maximo": 240},
    "duracao_dias": {"minimo": 3, "maximo": 30}, "max_conexoes": 1,
    "preco_bom": {"nacional": {"so_ida": 350, "ida_e_volta": 650},
                  "internacional": {"so_ida": 1800}, "por_destino": {"REC": {"so_ida": 100}},
                  "queda_sobre_historico_pct": 30},
    "promocoes": {"cidades": ["Aracaju", "Salvador"], "bonus_transferencia": True,
                  "palavras_titulo": ["passagens", "milhas"]},
}


def oferta(dest="GRU", preco=300.0, ida=date(2027, 1, 10), volta=None, intl=False):
    return Oferta("AJU", dest, ida, volta, preco, internacional=intl)


def test_seleciona_abaixo_do_teto_e_fica_com_a_data_mais_barata():
    e = Estado(":memory:")
    boas = selecionar([oferta(preco=340), oferta(preco=290, ida=date(2027, 2, 1)), oferta(preco=500, dest="GIG")], CFG, e)
    assert [(o.destino, o.preco) for o in boas] == [("GRU", 290)]
    assert "teto" in boas[0].motivo


def test_teto_por_destino_tem_prioridade():
    assert selecionar([oferta(dest="REC", preco=150)], CFG, Estado(":memory:")) == []


def test_queda_sobre_historico():
    e = Estado(":memory:")
    for _ in range(8):
        e.registrar_preco("AJU-GIG", "so_ida", 1000)
    boas = selecionar([oferta(dest="GIG", preco=650)], CFG, e)
    assert len(boas) == 1 and "35%" in boas[0].motivo


def test_nao_repete_alerta_mas_realerta_se_cair():
    e = Estado(":memory:")
    o = oferta(preco=300)
    e.marcar_enviado(o.chave, 300)
    assert selecionar([oferta(preco=295)], CFG, e) == []
    assert len(selecionar([oferta(preco=260)], CFG, e)) == 1


def test_radar_filtra_janela_conexoes_e_duracao():
    r = Radar("x", CFG)
    r.cidades._mapa = {"GRU": {"nome": "São Paulo", "pais": "BR"}, "LIS": {"nome": "Lisboa", "pais": "PT"}}
    def falso(origem, so_ida):
        base = {"origin": origem, "value": 200, "number_of_changes": 0}
        if so_ida:
            return [dict(base, destination="GRU", depart_date="2027-01-20"),
                    dict(base, destination="GRU", depart_date="2027-01-03"),          # perto demais
                    dict(base, destination="LIS", depart_date="2027-02-01", number_of_changes=3),
                    {"lixo": 1}]
        return [dict(base, destination="LIS", depart_date="2027-03-01", return_date="2027-03-10"),
                dict(base, destination="LIS", depart_date="2027-03-01", return_date="2027-03-02")]
    r._ultimos_precos = falso
    achadas = r.buscar(hoje=date(2027, 1, 1))
    assert [(o.destino, o.tipo, o.internacional) for o in achadas] == [("GRU", "so_ida", False), ("LIS", "ida_e_volta", True)]
    assert achadas[0].nome_destino == "São Paulo"


def test_links_de_milhas():
    cfg = {"programas": ["smiles", "azul"], "links": {
        "smiles": {"so_ida": "https://s/?d={ida_ms}&o={origem}&x={destino}"},
        "azul": {"so_ida": "https://a/?std={ida_mdy}", "ida_e_volta": "https://a/?std={ida_mdy}&v={volta_mdy}"}}}
    rs = milhas.links_para(cfg, "AJU", "GRU", date(2027, 1, 10), None)
    assert rs[0].link == "https://s/?d=1799593200000&o=AJU&x=GRU"
    assert rs[1].link == "https://a/?std=01/10/2027"
    assert milhas.links_para(cfg, "AJU", "GRU", date(2027, 1, 10), date(2027, 1, 15))[1].link.endswith("v=01/15/2027")


def test_extrai_menor_milha_e_classifica():
    txt = "Voo G3 1234 06:10 12.500 milhas + R$ 45,00\nVoo 9.800 milhas\n Azul 22000 pontos  R$ 1.200"
    assert milhas.extrair_milhas(txt) == 9800
    assert milhas.classificar("Ops! Não encontramos voos para essa data") == ("sem_voo", None)
    assert milhas.classificar("Confirme que não sou um robô")[0] == "bloqueado"
    assert milhas.classificar("carregando...")[0] == "sem_leitura"
    assert milhas.extrair_milhas("ganhe 500 milhas") is None


FEED = """<rss><channel>
<item><title>Passagens para Aracaju a partir de R$ 199</title><link>http://x/1</link><description>&lt;p&gt;oi&lt;/p&gt;</description></item>
<item><title>Azul oferece 120% de bônus nas transferências da Livelo</title><link>http://x/2</link><description>a</description></item>
<item><title>Gol tem passagens nacionais a partir de R$ 305</title><link>http://x/3</link><description>a</description></item>
<item><title>Sala VIP em Guarulhos</title><link>http://x/4</link><description>Salvadores da pátria</description></item>
</channel></rss>"""


def test_promos():
    itens = promos.ler_feed(FEED)
    abertos = []
    def baixar(link):
        abertos.append(link)
        return "saindo de São Paulo, Salvador e Recife"
    r = [promos.avaliar(i, CFG, ["azul", "smiles"], baixar) for i in itens]
    assert r[0].motivo == "cita Aracaju"
    assert r[1].motivo == "bônus de transferência"
    assert r[2].motivo == "o post cita Salvador"
    assert r[3] is None and abertos == ["http://x/3"]


def test_mensagem():
    o = oferta(preco=300)
    o.nome_destino, o.motivo, o.voo, o.hora_ida = "São Paulo", "abaixo do teto de R$ 350", "G31234", "06:10"
    a = milhas.ResultadoMilhas("smiles", "https://s/?a=1&b=2", "ok", 9800)
    b = milhas.ResultadoMilhas("azul", "https://a/", "bloqueado")
    o.milhas = [a, b]
    t = texto_oferta(o, "Aracaju", {"smiles": 16})
    assert "Aracaju (AJU) → São Paulo (GRU)" in t and "R$ 300" in t and "G31234 às 06:10" in t
    assert "9.800" in t and "≈ R$ 157" in t and "a=1&amp;b=2" in t
    assert "pediu verificação" in t and "Compensa mais em milhas" in t


def test_meses_e_resolucao_de_cidade():
    from robo.radar import Cidades
    from robo.web import lista_meses
    assert lista_meses("2026-11", "2027-02") == ["2026-11", "2026-12", "2027-01", "2027-02"]
    assert lista_meses("2027-03", "2027-01") == []
    assert len(lista_meses("2027-01", "2030-01")) == 12
    c = Cidades()
    c._mapa = {"SAO": {"nome": "São Paulo", "pais": "BR"}, "SSA": {"nome": "Salvador", "pais": "BR"},
               "SAL": {"nome": "San Salvador", "pais": "SV"}, "LIS": {"nome": "Lisboa", "pais": "PT"}}
    assert c.resolver("sao paulo") == "SAO" and c.resolver("lis") == "LIS" and c.resolver("Lisb") == "LIS"
    assert c.resolver("Salvador") == "SSA" and c.resolver("Atlântida") is None


def test_melhor_do_mes_ignora_outro_mes_e_conexoes_demais():
    r = Radar("x", CFG)
    r.cidades._mapa = {}
    r._v3 = lambda *a, **k: [
        {"price": 100, "departure_at": "2027-02-03T08:00:00-03:00", "transfers": 4, "airline": "G3"},
        {"price": 90, "departure_at": "2027-03-01T08:00:00-03:00", "transfers": 0},
        {"price": 300, "departure_at": "2027-02-14T06:15:00-03:00", "transfers": 0, "airline": "AD", "flight_number": 4100},
        {"price": 350, "departure_at": "2027-02-20T06:15:00-03:00", "transfers": 0},
    ]
    o = r.melhor_do_mes("AJU", "GRU", "2027-02", hoje=date(2027, 1, 1))
    assert (o.ida, o.preco, o.voo, o.hora_ida) == (date(2027, 2, 14), 300, "AD4100", "06:15")


def test_app_exige_login_e_isola_buscas(tmp_path):
    from robo.web import criar_app, criar_usuario
    criar_usuario(tmp_path / "a.db", "Ana", "segredo123")
    r = Radar("x", CFG)
    r.cidades._mapa = {"GRU": {"nome": "São Paulo", "pais": "BR"}, "AJU": {"nome": "Aracaju", "pais": "BR"},
                       "SSA": {"nome": "Salvador", "pais": "BR"}}
    app = criar_app(dict(CFG, milhas={"ativo": False}), tmp_path / "a.db", fabrica_radar=lambda: r)
    app.executor.enfileirar = lambda i: None
    c = app.test_client()
    assert c.get("/").status_code == 302 and c.get("/api/busca/1").status_code == 401
    assert c.post("/login", data={"usuario": "ana", "senha": "segredo123"}).status_code == 400  # sem csrf
    with c.session_transaction() as s:
        s["csrf"] = "t"
    assert c.post("/login", data={"usuario": "ANA", "senha": "segredo123", "csrf": "t"}).status_code == 302
    with c.session_transaction() as s:
        s["csrf"] = "t"
    resp = c.post("/buscar", data={"origem": "Aracaju, ssa", "destino": "GRU", "inicio": "2027-01", "fim": "2027-02", "csrf": "t"})
    assert resp.status_code == 302 and c.get("/api/busca/1").json["meses"] == ["2027-01", "2027-02"]
    assert set(c.get("/api/busca/1").json["nomes"]) == {"AJU", "SSA", "GRU"}
    with c.session_transaction() as s:
        s["csrf"] = "t"
    ruim = c.post("/buscar", data={"origem": "Atlântida", "destino": "GRU", "inicio": "2027-01", "fim": "2027-02", "csrf": "t"})
    assert "Não reconheci a origem" in ruim.get_data(as_text=True)
    outro = app.test_client()
    with outro.session_transaction() as s:
        s["usuario"] = "beto"
    assert outro.get("/api/busca/1").status_code == 404


GOL_TEXTO = """Ida: Salvador a São Paulo Sáb, 26 Dez 2026
Qua, 23 Dez
A partir de BRL 567,52
Qui, 24 Dez
A partir de BRL 390,52
Sex, 25 Dez
A partir de BRL 390,52
Sáb, 26 Dez
A partir de BRL 436,52
Dom, 27 Dez
A partir de BRL 567,52
Seg, 28 Dez
A partir de BRL 567,52
Ter, 29 Dez
A partir de BRL 1.390,52
03:40 SSA Salvador 06:15 CGH A partir de BRL 436,52 por viajante"""


def test_le_calendario_da_gol():
    dias = milhas.ler_calendario(GOL_TEXTO, date(2026, 12, 26))
    assert dias[date(2026, 12, 25)] == 390.52 and dias[date(2026, 12, 26)] == 436.52
    assert dias[date(2026, 12, 29)] == 1390.52 and len(dias) == 7
    virada = milhas.ler_calendario("Qui, 31 Dez\nBRL 500,00\nSex, 1 Jan\nBRL 450,00", date(2026, 12, 31))
    assert virada == {date(2026, 12, 31): 500.0, date(2027, 1, 1): 450.0}


class NavFalso:
    """Simula o site da companhia: cada dia custa 500, exceto os dias especiais."""
    especiais = {date(2027, 3, 9): 390.0, date(2027, 3, 27): 300.0}

    def __init__(self, *a, **k):
        self.abertos = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def ler_site_cia(self, link, ref, rotulo=""):
        self.abertos.append(ref)
        from datetime import timedelta
        dias = [ref + timedelta(days=i) for i in range(-3, 4)]
        return {d: self.especiais.get(d, 500.0) for d in dias}

    def consultar(self, r, rotulo=""):
        r.status, r.milhas = "ok", 10000
        return r


def _app_com_busca(tmp_path, monkeypatch, varredura, modo="robo"):
    import json as _json
    from robo import web
    monkeypatch.setattr(web.mod_milhas, "Navegador", NavFalso)
    monkeypatch.setattr(web.time, "sleep", lambda s: None)
    monkeypatch.setattr(web.random, "uniform", lambda a, b: 0)
    r = Radar("x", CFG)
    r.cidades._mapa = {}
    r.melhor_do_mes = lambda de, para, mes: (
        Oferta(de, para, date(2027, 3, 10), None, 410.0, cia="G3", voo="G31", hora_ida="05:20") if mes == "2027-03" else None)
    cfg = dict(CFG, modo_sites=modo, companhias={"G3": {"nome": "GOL", "link": "https://gol/?d={ida}&o={origem}"}},
               milhas={"ativo": True, "programas": ["smiles"], "valor_milheiro": {"smiles": 16},
                       "links": {"smiles": {"so_ida": "https://s/{ida}"}}})
    db = tmp_path / "a.db"
    ex = web.Executor(cfg, db, fabrica_radar=lambda: r)
    with web.closing(web.conectar(db)) as c, c:
        c.execute("INSERT INTO buscas(usuario,origens,destino,nome_destino,meses,com_milhas,status,ida_volta,varredura) "
                  "VALUES('a',?,?,?,?,1,'na_fila',1,?)", (_json.dumps(["AJU"]), "GRU", "SP", _json.dumps(["2027-03", "2027-04"]), varredura))
    ex.executar(1)
    if modo != "robo":
        return cfg, db, r
    with web.closing(web.conectar(db)) as c:
        return [dict(x) for x in c.execute("SELECT * FROM resultados ORDER BY trecho")], \
            c.execute("SELECT status FROM buscas").fetchone()[0]


def test_confere_no_site_e_troca_pelo_dia_vizinho_mais_barato(tmp_path, monkeypatch):
    linhas, status = _app_com_busca(tmp_path, monkeypatch, 0)
    assert status == "pronto" and [l["trecho"] for l in linhas] == ["ida", "volta"]
    ida = linhas[0]
    assert (ida["ida"], ida["preco"], ida["preco_radar"]) == ("2027-03-09", 390.0, 410.0)
    assert "indicava 10/03" in ida["conferido"] and ida["link_cia"] == "https://gol/?d=2027-03-09&o=AJU"
    assert linhas[1]["de"] == "GRU" and linhas[1]["para"] == "AJU"
    m = __import__("json").loads(ida["milhas"])
    assert m[0]["milhas"] == 10000 and m[0]["link"] == "https://s/2027-03-09" and m[0]["reais"] == 160


def test_varredura_acha_dia_fora_do_alcance_do_radar(tmp_path, monkeypatch):
    linhas, status = _app_com_busca(tmp_path, monkeypatch, 1)
    assert status == "pronto"
    marco = [l for l in linhas if l["mes"] == "2027-03" and l["trecho"] == "ida"][0]
    assert (marco["ida"], marco["preco"]) == ("2027-03-27", 300.0) and "varredura" in marco["conferido"]
    abril = [l for l in linhas if l["mes"] == "2027-04"]
    assert len(abril) == 2 and all(l["preco"] == 500.0 for l in abril)  # o radar não tinha abril


def test_modo_assistido_le_o_que_a_extensao_manda(tmp_path, monkeypatch):
    import json as _json
    from robo import web
    hoje = date.today
    cfg, db, r = _app_com_busca(tmp_path, monkeypatch, 0, modo="assistido")
    with web.closing(web.conectar(db)) as c:
        linhas = [dict(x) for x in c.execute("SELECT * FROM resultados ORDER BY trecho")]
        assert c.execute("SELECT status FROM buscas").fetchone()[0] == "pronto"
    ida = linhas[0]
    assert ida["ida"] == "2027-03-10" and ida["conferido"] is None  # nada aberto pelo robô
    assert [m["status"] for m in _json.loads(ida["milhas"])] == ["abrir"]

    app = web.criar_app(cfg, db, fabrica_radar=lambda: r)
    c = app.test_client()
    with c.session_transaction() as s:
        s["usuario"] = "a"
    rid = ida["id"]
    with web.closing(web.conectar(db)) as cx, cx:
        cx.execute("INSERT OR IGNORE INTO usuarios(nome, senha_hash) VALUES('a','x')")
    chave = web.chave_do_usuario(db, "a")
    H = {"X-Radar-Chave": chave}
    # sem ter aberto pelo programa, a leitura é ignorada
    sem = c.post("/api/captura", headers=H, json={"url": "https://b2c.voegol.com.br/x", "texto": "Ter, 9 Mar BRL 390,00"}).json
    assert sem["lido"] is False
    # abre o site da companhia pelo programa e a extensão manda o texto da página
    resp = c.get(f"/abrir/{rid}/cia")
    assert resp.status_code == 302 and resp.location == "https://gol/?d=2027-03-10&o=AJU"
    app_sites = {"https://gol/": "voegol.com.br"}
    texto = "Seg, 8 Mar\nA partir de BRL 450,00\nTer, 9 Mar\nA partir de BRL 390,00\nQua, 10 Mar\nA partir de BRL 436,52"
    # o link de teste não é de um domínio real; registra a espera como se fosse a GOL
    monkeypatch.setattr(web, "chave_site", lambda u: "gol")
    c.get(f"/abrir/{rid}/cia")
    lido = c.post("/api/captura", headers=H, json={"url": "https://b2c.voegol.com.br/compra", "texto": texto}).json
    assert lido["lido"] is True and "390,00" in lido["mensagem"]
    d = c.get("/api/busca/1").json["resultados"][0]
    assert (d["ida"], d["preco"], d["preco_radar"]) == ("2027-03-09", 390.0, 410.0)
    assert d["conferido"].startswith("lido por você") and d["link_cia"] == "https://gol/?d=2027-03-09&o=AJU"
    assert d["milhas"][0]["link"] == "https://s/2027-03-09"  # milhas seguem o dia novo
    # milhas: abre a Smiles pelo programa e a extensão manda os dados que a página carregou
    monkeypatch.setattr(web, "chave_site", lambda u: "smiles")
    c.get(f"/abrir/{rid}/smiles")
    js = [{"url": "https://api/x", "dados": {"flights": [{"fare": {"miles": 14500}}, {"fare": {"miles": 11200}}],
                                             "milesBalance": 3000}}]
    lido = c.post("/api/captura", headers=H, json={"url": "https://www.smiles.com.br/x", "texto": "carregando", "json": js}).json
    assert lido["lido"] is True
    m = c.get("/api/busca/1").json["resultados"][0]["milhas"][0]
    assert (m["status"], m["milhas"], m["reais"]) == ("ok", 11200, 179)
    # sem chave, ou com chave errada, recusa
    assert c.post("/api/captura", json={}).status_code == 403
    assert c.post("/api/captura", headers={"X-Radar-Chave": "errada"}, json={}).status_code == 403
    # a chave de outro usuário não mexe nas buscas deste
    with web.closing(web.conectar(db)) as cx, cx:
        cx.execute("INSERT INTO usuarios(nome, senha_hash) VALUES('b','x')")
    outra = {"X-Radar-Chave": web.chave_do_usuario(db, "b")}
    r2 = c.post("/api/captura", headers=outra, json={"url": "https://www.smiles.com.br/x", "texto": "9.000 milhas"}).json
    assert r2["lido"] is False
    # a página logada traz a chave para a extensão
    assert chave in c.get("/").get_data(as_text=True)


def test_chave_site():
    from robo.web import chave_site
    assert chave_site("https://b2c.voegol.com.br/compra") == "gol"
    assert chave_site("https://www.latamairlines.com/br/pt") == "latam"
    assert chave_site("https://voeazul.com.br/x") == "azul"
    assert chave_site("https://smiles.com.br.golpe.net/") is None


def test_le_lista_de_voos_da_latam():
    texto = """Escolha um voo de ida  Recomendado Mais econômico 10:45 AJU Duração 2 h 50 min. 13:35 GRU
    Por pessoa a partir de BRL 459,94 Inclui taxas e impostos  Comece a acumular milhas
    3:30 AJU 6:25 GRU Por pessoa a partir de BRL 459,94   Por pessoa a partir de BRL 968,85"""
    assert milhas.menor_preco_pagina(texto) == 459.94
    assert milhas.ler_calendario(texto, date(2026, 12, 25)) == {}
    assert milhas.menor_preco_pagina("Ganhe R$ 10,00 de desconto") is None


def test_resumo_da_gol_ignora_oferta_de_upgrade():
    texto = """Resumo da viagem Ida: Salvador a São Paulo Sex, 25 Dez 2026 Editar seleção
    09:55 SSA Direto 2h 30min 12:25 GRU Operado por GOL Light BRL 390,52 por viajante
    Última chance! Voe com mais flexibilidade por apenas BRL 119,00 a mais por passageiro. Mudar para Classic
    Sem surpresas! Todos os impostos estão incluídos no preço total. Total da sua reserva: BRL 390,52"""
    assert milhas.menor_preco_pagina(texto) == 390.52
    assert milhas.menor_preco_pagina("Bagagem extra por BRL 89,90") is None


def test_login_bloqueia_depois_de_muitas_tentativas(tmp_path, monkeypatch):
    from robo.web import criar_app, criar_usuario
    criar_usuario(tmp_path / "a.db", "ana", "segredo123")
    criar_usuario(tmp_path / "a.db", "ana", "nova-senha-456")  # trocar a senha mantém o usuário
    r = Radar("x", CFG)
    r.cidades._mapa = {}
    app = criar_app(dict(CFG, milhas={"ativo": False}), tmp_path / "a.db", fabrica_radar=lambda: r)
    import robo.web as web
    monkeypatch.setattr(web.time, "sleep", lambda s: None)
    c = app.test_client()
    def tentar(senha):
        with c.session_transaction() as s:
            s["csrf"] = "t"
        return c.post("/login", data={"usuario": "ana", "senha": senha, "csrf": "t"})
    assert tentar("segredo123").status_code == 200  # senha antiga não vale mais
    for _ in range(9):
        tentar("errada")
    assert tentar("nova-senha-456").status_code == 429  # bloqueado mesmo com a senha certa
