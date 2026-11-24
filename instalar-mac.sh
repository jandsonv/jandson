#!/bin/bash
# Instalador do Radar de Passagens para Mac, em um comando só:
#
#   curl -fsSL https://raw.githubusercontent.com/jandsonv/jandson/refs/heads/claude/focused-wright-zesq1r/instalar-mac.sh | bash
#
# Baixa o programa para ~/RadarPassagens, instala o Python e as dependências
# (sem pedir senha de administrador), pergunta o token do Travelpayouts e o seu
# usuário, cria um atalho na Mesa e abre o programa. Rodar de novo atualiza,
# mantendo token, usuários e buscas.
set -euo pipefail

REPO="jandsonv/jandson"
BRANCH="${RADAR_BRANCH:-claude/focused-wright-zesq1r}"
DESTINO="${RADAR_DIR:-$HOME/RadarPassagens}"
ATALHO_DIR="${RADAR_ATALHO_DIR:-$HOME/Desktop}"

azul() { printf '\n\033[1;34m%s\033[0m\n' "$*"; }
aviso() { printf '\033[33m%s\033[0m\n' "$*"; }

# Lê do teclado mesmo quando o script chega por "curl | bash".
perguntar() { # $1 = pergunta, $2 = variável de ambiente com resposta pronta
  local pronto="${!2:-}"
  if [ -n "$pronto" ]; then printf '%s' "$pronto"; return; fi
  if [ -r /dev/tty ]; then
    local r; printf '%s' "$1" > /dev/tty; IFS= read -r r < /dev/tty || true; printf '%s' "$r"
  fi
}

azul "1/6 · Preparando o instalador de Python (uv)"
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
uv --version

azul "2/6 · Baixando o programa para $DESTINO"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
curl -fsSL "https://codeload.github.com/$REPO/tar.gz/refs/heads/$BRANCH" -o "$TMP/radar.tgz"
mkdir -p "$TMP/x" && tar -xzf "$TMP/radar.tgz" -C "$TMP/x" --strip-components=1
mkdir -p "$DESTINO"
if [ -f "$DESTINO/config.yaml" ] && ! cmp -s "$TMP/x/config.yaml" "$DESTINO/config.yaml"; then
  cp "$DESTINO/config.yaml" "$DESTINO/config.anterior.yaml"
  echo "Suas configurações antigas foram guardadas em config.anterior.yaml."
fi
# Copia o programa por cima; .env, dados/ e perfil-navegador/ não vêm no download e ficam intactos.
cp -R "$TMP/x/." "$DESTINO/"
cd "$DESTINO"
chmod +x ./*.command ./*.sh 2>/dev/null || true

azul "3/6 · Instalando Python e dependências (pode levar alguns minutos)"
[ -x .venv/bin/python ] || uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt

azul "4/6 · Navegador para a consulta de milhas"
if [ -d "/Applications/Google Chrome.app" ] || [ -d "$HOME/Applications/Google Chrome.app" ]; then
  echo "Google Chrome encontrado."
else
  echo "Google Chrome não encontrado; baixando um navegador próprio para o robô."
  .venv/bin/python -m playwright install chromium \
    || aviso "Não consegui baixar o navegador. As buscas funcionam, mas as milhas virão só como link. Instale o Google Chrome e rode o instalador de novo."
fi

azul "5/6 · Token do Travelpayouts"
[ -f .env ] || cp .env.example .env
TOKEN_ATUAL="$(grep -E '^TRAVELPAYOUTS_TOKEN=' .env | cut -d= -f2- || true)"
if [ -n "$TOKEN_ATUAL" ] && [ -z "${RADAR_TOKEN:-}" ]; then
  echo "Já configurado."
else
  TOKEN="$(perguntar 'Cole o API token do Travelpayouts e aperte Enter (ou só Enter para pular): ' RADAR_TOKEN)"
  TOKEN="$(printf '%s' "$TOKEN" | tr -d '[:space:]')"
  if [ -n "$TOKEN" ]; then
    CODIGO="$(curl -s -o /dev/null -w '%{http_code}' -H "X-Access-Token: $TOKEN" \
      'https://api.travelpayouts.com/v2/prices/latest?origin=AJU&currency=brl&limit=1' || true)"
    if [ "$CODIGO" = "401" ]; then
      aviso "O Travelpayouts recusou esse token. Confira e rode o instalador de novo."
    else
      grep -v '^TRAVELPAYOUTS_TOKEN=' .env > .env.tmp || true
      printf 'TRAVELPAYOUTS_TOKEN=%s\n' "$TOKEN" >> .env.tmp && mv .env.tmp .env
      chmod 600 .env
      echo "Token salvo."
    fi
  else
    aviso "Sem token, a busca não traz preços. Depois é só rodar o instalador de novo."
  fi
fi

azul "6/6 · Seu usuário do programa"
TEM_USUARIO="$(.venv/bin/python - <<'PY'
from contextlib import closing
from robo.config import DADOS
from robo.web import conectar
with closing(conectar(DADOS / "app.db")) as db:
    print("sim" if db.execute("SELECT 1 FROM usuarios").fetchone() else "nao")
PY
)"
if [ "$TEM_USUARIO" = "sim" ] && [ -z "${RADAR_USUARIO:-}" ]; then
  echo "Já existe usuário. Para trocar a senha: cd ~/RadarPassagens && .venv/bin/python -m robo criar-usuario SEUNOME"
else
  NOME="$(perguntar 'Escolha um nome de usuário: ' RADAR_USUARIO)"
  NOME="${NOME:-$(whoami)}"
  if [ -n "${RADAR_SENHA:-}" ]; then
    .venv/bin/python -m robo criar-usuario "$NOME" --senha "$RADAR_SENHA"
  elif [ -r /dev/tty ]; then
    until .venv/bin/python -m robo criar-usuario "$NOME" < /dev/tty > /dev/tty 2>&1; do
      echo "Vamos tentar de novo." > /dev/tty
    done
  else
    aviso "Crie seu usuário depois: cd ~/RadarPassagens && .venv/bin/python -m robo criar-usuario SEUNOME"
  fi
fi

ATALHO="$ATALHO_DIR/Radar de Passagens.command"
if [ -d "$ATALHO_DIR" ]; then
  printf '#!/bin/bash\nexec "%s/abrir-mac.command"\n' "$DESTINO" > "$ATALHO"
  printf '#!/bin/bash\nexec "%s/entrar-milhas-mac.command"\n' "$DESTINO" > "$ATALHO_DIR/Radar - entrar nas milhas.command"
  chmod +x "$ATALHO" "$ATALHO_DIR/Radar - entrar nas milhas.command"
fi

if [ ! -f dados/.extensao_mostrada ] && command -v open >/dev/null 2>&1; then
  azul "Falta 1 passo: a extensão do Chrome (só na primeira vez)"
  echo "1) No Chrome, abra o endereço chrome://extensions"
  echo "2) Ligue o 'Modo do desenvolvedor' (canto superior direito)"
  echo "3) Clique em 'Carregar sem compactação' e escolha a pasta que acabou de abrir: $DESTINO/extensao"
  open "$DESTINO/extensao" || true
  mkdir -p dados && touch dados/.extensao_mostrada
fi

# Liga sozinho com o Mac (LaunchAgent): roda em segundo plano, sem Terminal, e volta se cair.
ROTULO="com.radarpassagens.app"
PLIST="$HOME/Library/LaunchAgents/$ROTULO.plist"
if [ "$(uname)" = "Darwin" ]; then
  azul "Deixando o Radar ligar sozinho com o Mac"
  # Para a versão antiga, se estiver rodando (pelo Terminal ou em segundo plano).
  launchctl bootout "gui/$(id -u)/$ROTULO" 2>/dev/null || true
  pkill -f "$DESTINO/.venv/bin/python -m robo app" 2>/dev/null || true
  mkdir -p "$HOME/Library/LaunchAgents" dados
  cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$ROTULO</string>
  <key>ProgramArguments</key>
  <array>
    <string>$DESTINO/.venv/bin/python</string><string>-m</string><string>robo</string>
    <string>app</string><string>--tailscale</string>
  </array>
  <key>WorkingDirectory</key><string>$DESTINO</string>
  <key>EnvironmentVariables</key><dict><key>PYTHONUNBUFFERED</key><string>1</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$DESTINO/dados/servidor.log</string>
  <key>StandardErrorPath</key><string>$DESTINO/dados/servidor.log</string>
</dict>
</plist>
PLIST
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  for _ in $(seq 1 30); do curl -s -o /dev/null -m 1 http://localhost:8000/login && break; sleep 0.5; done
  if curl -s -o /dev/null -m 2 http://localhost:8000/login; then
    echo "O Radar está ligado e vai ligar sozinho sempre que o Mac ligar."
  else
    aviso "O Radar não respondeu. Veja o registro em $DESTINO/dados/servidor.log"
  fi
  if [ -d "$ATALHO_DIR" ]; then
    printf '#!/bin/bash\nexec "%s/parar-mac.command"\n' "$DESTINO" > "$ATALHO_DIR/Radar - desligar.command"
    chmod +x "$ATALHO_DIR/Radar - desligar.command"
  fi
fi

azul "Pronto!"
echo "O Radar roda sozinho em segundo plano: pode fechar esta janela do Terminal."
echo "Para abrir: dois cliques em 'Radar de Passagens' na Mesa, ou http://localhost:8000 no Chrome."
if [ -z "${RADAR_NAO_ABRIR:-}" ] && command -v open >/dev/null 2>&1; then
  open "http://localhost:8000"
fi
