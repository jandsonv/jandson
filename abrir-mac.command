#!/bin/bash
# Abre o Radar de Passagens no navegador. O programa já roda sozinho em segundo plano;
# se por algum motivo não estiver rodando, liga de novo.
cd "$(dirname "$0")" || exit 1
ROTULO="com.radarpassagens.app"
if ! curl -s -o /dev/null -m 2 http://localhost:8000/login; then
  launchctl kickstart -k "gui/$(id -u)/$ROTULO" 2>/dev/null \
    || launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/$ROTULO.plist" 2>/dev/null
  for _ in $(seq 1 20); do curl -s -o /dev/null -m 1 http://localhost:8000/login && break; sleep 0.5; done
fi
open "http://localhost:8000"
