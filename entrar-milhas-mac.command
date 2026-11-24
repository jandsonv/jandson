#!/bin/bash
# Abre a janela do robô na Smiles, LATAM Pass e Azul para você entrar na conta.
# Feche o programa (Ctrl+C na janela dele) antes de usar isto.
cd "$(dirname "$0")" || exit 1
.venv/bin/python -m robo entrar-milhas
