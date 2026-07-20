#!/usr/bin/env bash
# Instalação local do Jarvis com uv: cria venv, instala e pré-baixa os modelos de voz.
set -euo pipefail

if ! command -v uv >/dev/null 2>&1; then
  echo "uv não encontrado. Instale: https://docs.astral.sh/uv/ (ou use pipx/venv)." >&2
  exit 1
fi

uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e .

echo "Baixando modelos de voz…"
.venv/bin/python -m jarvis.download

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Criei .env a partir do exemplo — preencha ANTHROPIC_API_KEY e JARVIS_TARGET_REPO."
fi

echo
echo "Pronto! Para rodar:"
echo "  source .venv/bin/activate && jarvis"
