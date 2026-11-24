#!/bin/bash
# Abre o programinha no navegador. Feche com Ctrl+C nesta janela.
cd "$(dirname "$0")" || exit 1
(sleep 2 && open "http://localhost:8000") &
.venv/bin/python -m robo app
