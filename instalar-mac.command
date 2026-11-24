#!/bin/bash
# Instalação no Mac. No Terminal: bash instalar-mac.command
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python não encontrado. Instale pelo site python.org/downloads e rode este arquivo de novo."
  exit 1
fi
python3 -m venv .venv || { echo "Falhou ao criar o ambiente do Python."; exit 1; }
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt || { echo "Falhou ao instalar as dependências."; exit 1; }
if [ ! -d "/Applications/Google Chrome.app" ] && [ ! -d "$HOME/Applications/Google Chrome.app" ]; then
  echo "Google Chrome não encontrado; baixando um navegador para a consulta de milhas..."
  .venv/bin/python -m playwright install chromium
fi
[ -f .env ] || cp .env.example .env
chmod +x abrir-mac.command rodar-mac.command 2>/dev/null
echo
echo "Instalado."
echo "1) Coloque o token do Travelpayouts no arquivo .env (open -e .env)"
echo "2) Crie seu usuário: .venv/bin/python -m robo criar-usuario seunome"
echo "3) Abra o programa: ./abrir-mac.command"
