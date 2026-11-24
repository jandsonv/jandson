"""Leitura do config.yaml e do .env."""
from __future__ import annotations

import os
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent.parent
DADOS = RAIZ / "dados"
SAIDA = RAIZ / "saida"
PERFIL = RAIZ / "perfil-navegador"


def carregar_env(caminho: Path | None = None) -> None:
    """Carrega pares CHAVE=valor do .env sem sobrescrever o ambiente."""
    caminho = caminho or RAIZ / ".env"
    if not caminho.exists():
        return
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, valor = linha.split("=", 1)
        valor = valor.strip().strip('"').strip("'")
        if valor:
            os.environ.setdefault(chave.strip(), valor)


def carregar_config(caminho: Path | None = None) -> dict:
    caminho = caminho or RAIZ / "config.yaml"
    cfg = yaml.safe_load(caminho.read_text(encoding="utf-8")) or {}
    cfg["origens"] = [str(o).upper() for o in cfg.get("origens", [])]
    if not cfg["origens"]:
        raise SystemExit("config.yaml: informe ao menos um aeroporto em 'origens'.")
    destinos = cfg.get("destinos", "qualquer")
    cfg["destinos"] = None if destinos in (None, "qualquer") else [str(d).upper() for d in destinos]
    return cfg


def segredo(nome: str, obrigatorio: bool = True) -> str:
    valor = os.environ.get(nome, "").strip()
    if obrigatorio and not valor:
        raise SystemExit(f"Falta {nome}. Preencha o arquivo .env (veja .env.example).")
    return valor
