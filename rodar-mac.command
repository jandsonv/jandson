#!/bin/bash
# Uma rodada dos alertas automáticos (Telegram). Para agendar a cada 3 horas:
#   crontab -e   e acrescente:   0 */3 * * * /caminho/da/pasta/rodar-mac.command
cd "$(dirname "$0")" || exit 1
mkdir -p dados
.venv/bin/python -m robo rodar "$@" >> dados/log.txt 2>&1
