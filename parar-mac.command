#!/bin/bash
# Desliga o Radar de Passagens (ele volta a ligar sozinho no próximo login do Mac).
launchctl bootout "gui/$(id -u)/com.radarpassagens.app" 2>/dev/null && echo "Radar desligado." || echo "O Radar já estava desligado."
