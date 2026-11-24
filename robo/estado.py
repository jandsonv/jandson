"""Memória do robô: histórico de preços e alertas já enviados (SQLite)."""
from __future__ import annotations

import sqlite3
import statistics
import time
from pathlib import Path


class Estado:
    def __init__(self, caminho: Path | str):
        if caminho != ":memory:":
            Path(caminho).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(caminho))
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS historico(
                rota TEXT, tipo TEXT, preco REAL, visto_em REAL);
            CREATE INDEX IF NOT EXISTS ix_hist ON historico(rota, tipo);
            CREATE TABLE IF NOT EXISTS enviados(
                chave TEXT PRIMARY KEY, preco REAL, enviado_em REAL);
            """
        )

    # --- histórico de preços por rota -------------------------------
    def registrar_preco(self, rota: str, tipo: str, preco: float) -> None:
        self.db.execute("INSERT INTO historico VALUES(?,?,?,?)", (rota, tipo, preco, time.time()))

    def mediana(self, rota: str, tipo: str, minimo_obs: int = 8) -> float | None:
        linhas = self.db.execute(
            "SELECT preco FROM historico WHERE rota=? AND tipo=? ORDER BY visto_em DESC LIMIT 200",
            (rota, tipo),
        ).fetchall()
        if len(linhas) < minimo_obs:
            return None
        return statistics.median(p for (p,) in linhas)

    # --- controle de repetição ---------------------------------------
    def deve_enviar(self, chave: str, preco: float | None = None, queda_pct: float = 10) -> bool:
        linha = self.db.execute("SELECT preco FROM enviados WHERE chave=?", (chave,)).fetchone()
        if linha is None:
            return True
        if preco is None or linha[0] is None:
            return False
        return preco <= linha[0] * (1 - queda_pct / 100)

    def marcar_enviado(self, chave: str, preco: float | None = None) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO enviados VALUES(?,?,?)", (chave, preco, time.time())
        )

    def salvar(self) -> None:
        self.db.commit()
