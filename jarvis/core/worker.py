"""Worker do back: um agente Claude (Sonnet/Opus) por tarefa, via Claude Agent SDK.

Fluxo: plano + validação (modo `plan`, termina em ExitPlanMode) → [crítica extra, só no Opus] →
execução (`acceptEdits`) → relatório + `RESUMO_FALADO`. Herda skills/MCPs/CLAUDE.md do usuário.

As ferramentas passam pelo risk gate: o arriscado vira um pedido de aprovação por voz e as
perguntas usam a tool `ask_user`. As duas coisas são mediadas pelo TaskManager → front.
"""

from __future__ import annotations

import logging
import re
import tempfile
import warnings
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..config import settings
from ..platform import detect
from .auth import sdk_env
from .risk import classify

if TYPE_CHECKING:
    from .tasks import Task, TaskManager

log = logging.getLogger(__name__)

# skills="all" pré-libera a tool Skill (intencional); o SDK avisa que isso "sombreia" o can_use_tool.
warnings.filterwarnings("ignore", message=r".*can_use_tool.*", category=Warning)

PLAN_PROMPT = """Brief da tarefa (vindo do assistente de voz):

{brief}

Fase 1: investigue e escreva o plano, depois valide-o com o checklist do protocolo Jarvis.
Ainda não altere nada. Quando o plano validado estiver pronto, apresente-o com ExitPlanMode."""

CRITIQUE_PROMPT = """Fase 2: critique o plano como um revisor sênior cético. Procure requisitos esquecidos,
suposições frágeis, riscos e uma forma mais simples de fazer. Ainda não altere nada. Apresente o plano
final revisado com ExitPlanMode."""

EXECUTE_PROMPT = """Fase final: execute o plano validado abaixo, verifique o resultado de verdade e termine
com o relatório e a linha RESUMO_FALADO.

{plan}"""

PLAN_RECEIVED = ("Plano registrado e aprovado. Encerre seu turno agora, sem escrever mais nada e sem executar: "
                 "a ordem de execução vem na próxima mensagem.")

_SUMMARY_RE = re.compile(r"^\s*RESUMO_FALADO:\s*(.+?)\s*$", re.M)


def back_prompt(cwd: str) -> str:
    tpl = resources.files("jarvis.prompts").joinpath("back.md").read_text()
    return tpl.format(platform=detect().describe(), cwd=cwd)


def parse_result(text: str) -> tuple[str, str]:
    """Separa (resumo falado, relatório)."""
    m = None
    for m in _SUMMARY_RE.finditer(text or ""):
        pass
    if not m:
        report = (text or "").strip()
        first = re.split(r"(?<=[.!?])\s", report, maxsplit=1)[0] if report else "Tarefa concluída."
        return first[:200], report
    return m.group(1), (text[:m.start()] + text[m.end():]).strip()


def _describe_tool(name: str, args: dict) -> str:
    if name == "Bash":
        return f"rodando: {args.get('command', '')[:80]}"
    if name in ("Edit", "Write", "MultiEdit"):
        return f"editando {Path(args.get('file_path', '')).name}"
    if name in ("Read", "Grep", "Glob"):
        return "lendo o código"
    if name.startswith("mcp__"):
        return f"usando {name.split('__')[-1]}"
    return name


def _build_servers(task: Task, manager: TaskManager) -> dict[str, Any]:
    from claude_agent_sdk import create_sdk_mcp_server, tool

    @tool("ask_user", "Faz uma pergunta curta ao usuário (por voz) e retorna a resposta dele.", {"question": str})
    async def ask_user(args: dict) -> dict:
        answer = await manager.request(task, "question", args["question"])
        return {"content": [{"type": "text", "text": str(answer)}]}

    desk = detect()

    @tool("open_path", "Abre arquivo, pasta ou URL no app padrão do desktop. side: auto|linux|windows.",
          {"target": str, "side": str})
    async def open_path(args: dict) -> dict:
        out = await desk.open_path(args["target"], args.get("side") or "auto")
        return {"content": [{"type": "text", "text": out}]}

    @tool("open_app", "Abre um aplicativo pelo nome. side: auto|linux|windows.", {"app": str, "side": str})
    async def open_app(args: dict) -> dict:
        out = await desk.open_app(args["app"], args.get("side") or "auto")
        return {"content": [{"type": "text", "text": out}]}

    @tool("media", "Controle de mídia: play_pause, next, previous, stop.", {"action": str})
    async def media(args: dict) -> dict:
        out = await desk.media(args["action"])
        return {"content": [{"type": "text", "text": out}]}

    return {
        "jarvis": create_sdk_mcp_server(name="jarvis", version="2.0.0", tools=[ask_user]),
        "desktop": create_sdk_mcp_server(name="desktop", version="2.0.0", tools=[open_path, open_app, media]),
    }


async def run_task(task: Task, manager: TaskManager) -> None:
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
    from claude_agent_sdk.types import (AssistantMessage, PermissionResultAllow, PermissionResultDeny,
                                        ResultMessage, ToolUseBlock)

    cfg = settings.back
    cwd = str(task.project) if task.project else tempfile.mkdtemp(prefix="jarvis-task-")
    roots = [Path(cwd).resolve()]

    plans: list[str] = []

    async def can_use_tool(tool_name: str, args: dict, _ctx: Any):
        if tool_name == "ExitPlanMode":
            # Aprovar aqui faria o CLI sair do modo plan e executar no mesmo turno. Em vez disso,
            # captura o plano e encerra o turno; a execução começa na fase seguinte.
            plans.append(str(args.get("plan", "")))
            return PermissionResultDeny(message=PLAN_RECEIVED)
        verdict, why = classify(tool_name, args, roots)
        if verdict == "allow":
            return PermissionResultAllow(updated_input=args)
        if verdict == "deny":
            log.warning("tarefa #%s: bloqueado %s %s", task.id, tool_name, str(args)[:200])
            return PermissionResultDeny(message=why)
        ok = await manager.request(task, "approval", why)
        if ok:
            return PermissionResultAllow(updated_input=args)
        return PermissionResultDeny(message="O usuário negou esta ação por voz. Siga sem ela ou pare e explique.")

    options = ClaudeAgentOptions(
        model=cfg.model_complex if task.tier == "complex" else cfg.model_default,
        fallback_model=cfg.fallback_model,
        effort=task.effort,  # type: ignore[arg-type]
        cwd=cwd,
        setting_sources=["user", "project", "local"],
        skills="all",
        system_prompt={"type": "preset", "preset": "claude_code", "append": back_prompt(cwd)},
        mcp_servers=_build_servers(task, manager),
        can_use_tool=can_use_tool,
        permission_mode="plan",
        env=sdk_env(),
    )

    async def turn(client: ClaudeSDKClient, prompt: str) -> str:
        await client.query(prompt)
        result = ""
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, ToolUseBlock):
                        task.activity = _describe_tool(block.name, block.input)
            elif isinstance(msg, ResultMessage):
                if msg.is_error:
                    raise RuntimeError(msg.result or msg.subtype)
                result = msg.result or ""
        return result

    async with ClaudeSDKClient(options=options) as client:
        task.status = "planejando"
        await turn(client, PLAN_PROMPT.format(brief=task.brief))
        if task.tier == "complex":
            await client.set_permission_mode("plan")
            await turn(client, CRITIQUE_PROMPT)
        task.status = "executando"
        await client.set_permission_mode("acceptEdits")
        plan = f"Plano validado:\n{plans[-1]}" if plans and plans[-1] else "(use o plano validado acima)"
        final = await turn(client, EXECUTE_PROMPT.format(plan=plan))
    task.spoken_summary, task.report = parse_result(final)
