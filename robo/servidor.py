"""Ponto de entrada do Radar de Passagens na internet (Railway ou outro servidor).

    gunicorn robo.servidor:app

Configuração por variáveis de ambiente:
    TRAVELPAYOUTS_TOKEN  token da API de preços
    RADAR_USUARIO        seu usuário (criado ou atualizado ao ligar)
    RADAR_SENHA          sua senha (mínimo 8 caracteres)
    RADAR_DADOS          pasta onde ficam buscas e usuários (um disco persistente, ex.: /data)
"""
from __future__ import annotations

import os

from werkzeug.middleware.proxy_fix import ProxyFix

from .config import DADOS, carregar_config, carregar_env
from .web import criar_app, criar_usuario

carregar_env()
cfg = carregar_config()
cfg["modo_sites"] = "assistido"  # no servidor não há navegador: a extensão do seu Chrome lê os sites

DADOS.mkdir(parents=True, exist_ok=True)
_usuario, _senha = os.environ.get("RADAR_USUARIO", "").strip(), os.environ.get("RADAR_SENHA", "")
if _usuario and _senha:
    criar_usuario(DADOS / "app.db", _usuario, _senha)

app = criar_app(cfg)
# O servidor fica atrás do proxy HTTPS do Railway: usa o endereço e o IP reais de quem acessa.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
if os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RADAR_HTTPS"):
    app.config["SESSION_COOKIE_SECURE"] = True
