"""Robô de passagens: python -m robo <comando>"""
from __future__ import annotations

import argparse
import sys
from datetime import date

from . import milhas as mod_milhas
from . import promos as mod_promos
from .config import DADOS, carregar_config, carregar_env, segredo
from .estado import Estado
from .radar import Radar, selecionar
from .telegram import Telegram, TelegramSimulado, texto_oferta, texto_promo


def _telegram(simular: bool):
    if simular:
        return TelegramSimulado()
    return Telegram(segredo("TELEGRAM_TOKEN"), segredo("TELEGRAM_CHAT_ID"))


def cmd_rodar(args) -> int:
    cfg = carregar_config()
    estado = Estado(DADOS / "estado.db")
    tg = _telegram(args.simular)
    cfg_m = cfg.get("milhas", {})
    usar_milhas = cfg_m.get("ativo", True) and not args.sem_milhas

    # 1) Radar em dinheiro
    token = segredo("TRAVELPAYOUTS_TOKEN", obrigatorio=False)
    radar = Radar(token, cfg)
    boas = []
    if token:
        todas = radar.buscar()
        boas = selecionar(todas, cfg, estado)
        estado.salvar()
        print(f"[radar] {len(todas)} preços vistos, {len(boas)} promoções novas.")
    else:
        print("[radar] sem TRAVELPAYOUTS_TOKEN no .env: pulando o radar de preços.")
    for o in boas:
        radar.detalhar(o)
        o.milhas = mod_milhas.links_para(cfg_m, o.origem, o.destino, o.ida, None) if cfg_m.get("ativo", True) else []

    # 2) Milhas, só nas datas que o radar apontou
    if usar_milhas and boas:
        limite = int(cfg_m.get("max_consultas_por_rodada", 9))
        fila = [(o, r) for o in boas for r in o.milhas][:limite]
        try:
            with mod_milhas.Navegador(cfg_m, args.chromium) as nav:
                for o, r in fila:
                    print(f"[milhas] {r.nome}: {o.rota} em {o.ida}")
                    nav.consultar(r, f"{o.rota}_{o.ida}")
        except Exception as e:
            print(f"[milhas] não consegui abrir o navegador ({type(e).__name__}: {str(e)[:200]}). "
                  "Os alertas seguem só com os links.")

    # 3) Alertas
    nome = radar.cidades.nome
    for o in boas:
        tg.enviar(texto_oferta(o, nome(o.origem), cfg_m.get("valor_milheiro", {})))
        if args.fotos:
            for r in o.milhas:
                if r.foto and r.status != "ok":
                    tg.enviar_foto(r.foto, f"{r.nome} · {o.rota} · {o.ida}")
        estado.marcar_enviado(o.chave, o.preco)
        estado.salvar()

    # 4) Sites de promoção
    promos = mod_promos.buscar(cfg, estado, cfg_m.get("programas", []))
    print(f"[promoções] {len(promos)} posts novos de interesse.")
    for p in promos:
        tg.enviar(texto_promo(p))
        estado.marcar_enviado(p.chave)
    estado.salvar()
    return 0


def cmd_milhas(args) -> int:
    """Consulta manual, para testar/calibrar a leitura dos sites."""
    cfg_m = carregar_config().get("milhas", {})
    ida = date.fromisoformat(args.data)
    resultados = mod_milhas.links_para(cfg_m, args.origem.upper(), args.destino.upper(), ida, None)
    if args.programa:
        resultados = [r for r in resultados if r.programa == args.programa]
    with mod_milhas.Navegador(cfg_m, args.chromium) as nav:
        for r in resultados:
            nav.consultar(r, f"{args.origem}-{args.destino}_{ida}".upper())
            valor = f"{r.milhas:,}".replace(",", ".") if r.milhas else "-"
            print(f"{r.nome:16} {r.status:12} {valor:>10}  {r.foto or ''}\n    {r.link}")
    return 0


def cmd_testar_telegram(args) -> int:
    _telegram(False).enviar("✅ Robô de passagens conectado. Os alertas vão chegar aqui.")
    print("Mensagem enviada. Confira o Telegram.")
    return 0


def cmd_telegram_id(args) -> int:
    conversas = Telegram(segredo("TELEGRAM_TOKEN"), "").conversas()
    if not conversas:
        print("Nenhuma conversa encontrada. Abra o seu bot no Telegram, mande 'oi' e rode de novo.")
        return 1
    for chat_id, nome in conversas:
        print(f"TELEGRAM_CHAT_ID={chat_id}   ({nome})")
    return 0


def cmd_app(args) -> int:
    from .web import conectar, criar_app

    with conectar(DADOS / "app.db") as db:
        if not db.execute("SELECT 1 FROM usuarios").fetchone():
            print("Nenhum usuário criado ainda. Rode antes: python -m robo criar-usuario SEU_NOME")
            return 1
    app = criar_app(carregar_config(), chromium=args.chromium)
    host = "0.0.0.0" if args.rede else "127.0.0.1"
    print(f"Abra no navegador: http://localhost:{args.porta}   (Ctrl+C para fechar)")
    from werkzeug.serving import run_simple

    run_simple(host, args.porta, app, threaded=True)
    return 0


def cmd_criar_usuario(args) -> int:
    import getpass

    from .web import criar_usuario

    senha = args.senha or getpass.getpass("Senha (mínimo 8 caracteres): ")
    if not args.senha and senha != getpass.getpass("Repita a senha: "):
        print("As senhas não conferem.")
        return 1
    try:
        criar_usuario(DADOS / "app.db", args.nome, senha)
    except ValueError as e:
        print(e)
        return 1
    print(f"Usuário '{args.nome.strip().lower()}' pronto.")
    return 0


def main(argv=None) -> int:
    for fluxo in (sys.stdout, sys.stderr):  # acentos e emojis no terminal do Windows
        if hasattr(fluxo, "reconfigure"):
            fluxo.reconfigure(encoding="utf-8", errors="replace")
    carregar_env()
    ap = argparse.ArgumentParser(prog="robo", description="Robô de passagens em promoção (dinheiro e milhas).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("rodar", help="faz uma rodada completa e envia os alertas")
    r.add_argument("--simular", action="store_true", help="mostra as mensagens na tela em vez de enviar")
    r.add_argument("--sem-milhas", action="store_true", help="não abre o navegador; manda só os links de milhas")
    r.add_argument("--fotos", action="store_true", help="envia a captura de tela quando não conseguir ler as milhas")
    r.add_argument("--chromium", help=argparse.SUPPRESS)
    r.set_defaults(f=cmd_rodar)

    m = sub.add_parser("milhas", help="consulta milhas numa rota e data (teste)")
    m.add_argument("origem")
    m.add_argument("destino")
    m.add_argument("data", help="AAAA-MM-DD")
    m.add_argument("--programa", choices=list(mod_milhas.NOMES))
    m.add_argument("--chromium", help=argparse.SUPPRESS)
    m.set_defaults(f=cmd_milhas)

    sub.add_parser("testar-telegram", help="envia uma mensagem de teste").set_defaults(f=cmd_testar_telegram)
    sub.add_parser("telegram-id", help="descobre o seu TELEGRAM_CHAT_ID").set_defaults(f=cmd_telegram_id)

    a = sub.add_parser("app", help="abre o programinha no navegador (login, busca por rota e mês)")
    a.add_argument("--porta", type=int, default=8000)
    a.add_argument("--rede", action="store_true", help="permite acessar de outros aparelhos da mesma rede Wi-Fi")
    a.add_argument("--chromium", help=argparse.SUPPRESS)
    a.set_defaults(f=cmd_app)

    u = sub.add_parser("criar-usuario", help="cria (ou troca a senha de) um usuário do programinha")
    u.add_argument("nome")
    u.add_argument("--senha", help=argparse.SUPPRESS)
    u.set_defaults(f=cmd_criar_usuario)

    args = ap.parse_args(argv)
    return args.f(args)


if __name__ == "__main__":
    sys.exit(main())
