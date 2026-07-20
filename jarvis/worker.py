"""Worker: coding agent real via Claude Agent SDK (`claude-agent-sdk`).

É o único componente que age no repositório. Herda skills/subagents/MCP/CLAUDE.md
locais e globais (`setting_sources`). O modelo e o effort vêm do handoff do Jarvis.

Ponte com o usuário (mediada pelo Jarvis, orquestrada pelo bridge):
  - ferramenta `ask_user`: o worker pergunta algo -> callback -> resposta.
  - `can_use_tool`: ações arriscadas (Bash/Edit/Write) pedem aprovação por voz.

Imports do SDK são preguiçosos para o módulo importar sem o pacote instalado.
As partes de decisão são funções puras, testáveis sem o SDK.
"""

from __future__ import annotations

import contextvars
from pathlib import Path
from typing import Any, Awaitable, Callable

from .config import settings
from .jarvis_brain import Handoff

# Callbacks injetados por tarefa (o bridge liga na camada de voz).
AskUserCb = Callable[[str], Awaitable[str]]
ApprovalCb = Callable[[str, dict], Awaitable[bool]]
OnMessage = Callable[[Any], Awaitable[None]]

_ask_user_cb: contextvars.ContextVar[AskUserCb] = contextvars.ContextVar("ask_user_cb")

_ASK_USER_GUIDE = (
    "\n\nIMPORTANTE: se precisar de qualquer informação, decisão ou esclarecimento "
    "do usuário, chame a ferramenta `ask_user` com sua pergunta e aguarde a resposta "
    "— NÃO encerre o turno pedindo input em texto."
)


async def _ask_user_impl(args: dict) -> dict:
    """Handler da tool `ask_user` (puro; lê o callback do contexto)."""
    cb = _ask_user_cb.get()
    answer = await cb(args["question"])
    return {"content": [{"type": "text", "text": answer}]}


async def _permission(
    tool_name: str,
    input_data: dict,
    *,
    allow_writes: bool,
    approval_cb: ApprovalCb,
) -> tuple[bool, str]:
    """Decisão pura de permissão. Retorna (permitido, motivo_da_negação).

    Ferramentas que alteram arquivos (`approval_required_tools`):
      - sem repositório alvo (allow_writes=False): negadas estruturalmente;
      - com repositório: pedem aprovação por voz.
    Demais ferramentas (leitura/raciocínio) são liberadas.
    """
    if tool_name in settings.approval_required_tools:
        if not allow_writes:
            return False, (
                "Para alterar arquivos eu preciso do caminho do repositório. "
                "Defina o repositório alvo na sessão e tente de novo."
            )
        ok = await approval_cb(tool_name, input_data)
        return ok, "" if ok else "Usuário não aprovou a ação por voz."
    return True, ""


def _build_user_interaction_server():
    """Cria o MCP server in-process com a tool `ask_user` (importa o SDK aqui)."""
    from claude_agent_sdk import create_sdk_mcp_server, tool

    ask_user = tool(
        "ask_user",
        "Faz uma pergunta ao usuário e retorna a resposta dele.",
        {"question": str},
    )(_ask_user_impl)
    return create_sdk_mcp_server(
        name="user_interaction", version="1.0.0", tools=[ask_user]
    )


async def run_task(
    handoff: Handoff,
    *,
    ask_user_cb: AskUserCb,
    approval_cb: ApprovalCb,
    on_message: OnMessage,
    cwd: Path | None = None,
) -> None:
    """Roda uma tarefa do worker até o fim, transmitindo mensagens via `on_message`.

    Cria um `ClaudeSDKClient` novo por tarefa com o modelo/effort do handoff
    (o SDK não expõe `set_effort()` dinâmico).

    `cwd` é o repositório alvo (opcional). Sem ele, o worker roda num diretório
    temporário e as ferramentas de escrita são negadas (modo conversa).
    """
    import tempfile

    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
    from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny

    _ask_user_cb.set(ask_user_cb)
    allow_writes = cwd is not None
    repo = str(cwd) if cwd is not None else tempfile.mkdtemp(prefix="jarvis-scratch-")

    async def can_use_tool(tool_name: str, input_data: dict, context: Any):
        ok, reason = await _permission(
            tool_name, input_data, allow_writes=allow_writes, approval_cb=approval_cb
        )
        if ok:
            return PermissionResultAllow(updated_input=input_data)
        return PermissionResultDeny(message=reason)

    options = ClaudeAgentOptions(
        model=handoff.model,
        effort=handoff.effort,
        fallback_model=settings.worker_fallback_model,
        cwd=repo,
        setting_sources=["user", "project", "local"],
        skills="all",
        mcp_servers={"user_interaction": _build_user_interaction_server()},
        can_use_tool=can_use_tool,
    )

    async with ClaudeSDKClient(options=options) as client:
        await client.query(handoff.prompt + _ASK_USER_GUIDE)
        async for message in client.receive_response():
            await on_message(message)
