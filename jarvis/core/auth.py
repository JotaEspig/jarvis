"""Autenticação: só assinatura (token do `claude setup-token`), nunca API key.

O Agent SDK executa o CLI do Claude Code. Se `ANTHROPIC_API_KEY` estiver no ambiente, o CLI a
prefere e a cobrança vai para a API, que é o que não queremos. Por isso a chave é sempre removida.
O token vem de `CLAUDE_CODE_OAUTH_TOKEN` ou do arquivo `~/.config/jarvis/token`. Sem token, o CLI
ainda pode usar o login interativo dele (`claude /login`), que também é assinatura.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from ..config import CONFIG_DIR

log = logging.getLogger(__name__)

TOKEN_FILE = CONFIG_DIR / "token"
_BLOCKED_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


@dataclass(frozen=True)
class AuthInfo:
    source: str  # "env" | "file" | "cli-login" | "none"
    token: str | None


def resolve_auth(token_file: Path = TOKEN_FILE) -> AuthInfo:
    token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
    if token:
        return AuthInfo("env", token)
    if token_file.is_file():
        token = token_file.read_text().strip()
        if token:
            return AuthInfo("file", token)
    if (Path.home() / ".claude" / ".credentials.json").is_file():
        return AuthInfo("cli-login", None)
    return AuthInfo("none", None)


def harden_environment() -> AuthInfo:
    """Remove as API keys do processo e exporta o token. Chamar no startup do core."""
    for var in _BLOCKED_ENV:
        if os.environ.pop(var, None) is not None:
            log.warning("%s removida do ambiente: o Jarvis usa só a assinatura.", var)
    info = resolve_auth()
    if info.token:
        os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = info.token
    elif info.source == "cli-login":
        log.warning("Sem CLAUDE_CODE_OAUTH_TOKEN; usando o login do Claude Code. Rode `claude setup-token`.")
    else:
        raise SystemExit(
            "Nenhuma credencial encontrada. Rode `claude setup-token` e exporte "
            f"CLAUDE_CODE_OAUTH_TOKEN ou salve o token em {TOKEN_FILE}."
        )
    return info


def sdk_env() -> dict[str, str]:
    """Ambiente extra passado ao CLI do SDK (garante que nenhuma API key vaze)."""
    env = {var: "" for var in _BLOCKED_ENV}
    token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if token:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    return env
