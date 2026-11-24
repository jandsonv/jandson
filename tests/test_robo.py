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
    r.cidades._mapa = {"GRU": {"nome": "São Paulo", "pais": "BR"}}
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
    resp = c.post("/buscar", data={"origens": "AJU", "destino": "GRU", "inicio": "2027-01", "fim": "2027-02", "csrf": "t"})
    assert resp.status_code == 302 and c.get("/api/busca/1").json["meses"] == ["2027-01", "2027-02"]
    outro = app.test_client()
    with outro.session_transaction() as s:
        s["usuario"] = "beto"
    assert outro.get("/api/busca/1").status_code == 404
