#!/usr/bin/env bash
# Instala o Jarvis num venv próprio, baixa os modelos e registra o serviço systemd de usuário.
# Linux nativo: core + edge.  WSL: só o core (rode deploy/install-windows-edge.ps1 no Windows).
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$PWD"
VENV="$HOME/.local/share/jarvis/venv"
CFG="$HOME/.config/jarvis"

command -v uv >/dev/null || { echo "Instale o uv: https://docs.astral.sh/uv/"; exit 1; }
command -v claude >/dev/null || { echo "Instale o Claude Code (o SDK usa o CLI): https://claude.com/claude-code"; exit 1; }

if grep -qi microsoft /proc/version; then MODE=wsl; EXTRAS="core"; UNIT=jarvis-core.service
else MODE=linux; EXTRAS="core,edge"; UNIT=jarvis.service
  pkg-config --exists ayatana-appindicator3-0.1 2>/dev/null && EXTRAS="$EXTRAS,tray"
fi
echo "→ modo: $MODE (extras: $EXTRAS)"

uv venv -q --python 3.11 "$VENV"
VIRTUAL_ENV="$VENV" uv pip install -q -e "$REPO[$EXTRAS]"
"$VENV/bin/jarvis" download "$([ $MODE = wsl ] && echo core || echo all)"

mkdir -p "$CFG"
if [ ! -s "$CFG/token" ] && [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
  echo "→ Gere um token da sua assinatura com:  claude setup-token"
  read -rsp "  Cole o token (fica em $CFG/token, permissão 600): " TOKEN; echo
  [ -n "$TOKEN" ] && (umask 077; printf '%s\n' "$TOKEN" > "$CFG/token")
fi
[ -f "$CFG/config.toml" ] || cp "$REPO/config.example.toml" "$CFG/config.toml"

mkdir -p "$HOME/.config/systemd/user"
cp "$REPO/deploy/$UNIT" "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user enable --now "$UNIT"
echo "✔ Jarvis instalado. Logs: journalctl --user -u ${UNIT%.service} -f   ·   diagnóstico: $VENV/bin/jarvis doctor"
