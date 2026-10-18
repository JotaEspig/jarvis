"""Front: o Haiku conversacional, numa sessão persistente do Agent SDK (latência baixa).

Sem tools nativas do Claude Code, só as tools MCP do Jarvis (despachar tarefa, responder ao
worker, encerrar conversa, ignorar fala...). Responde em streaming; o texto vai direto ao TTS.
"""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from dataclasses import dataclass, field
from importlib import resources
from typing import TYPE_CHECKING, Any, AsyncIterator

from ..config import FrontConfig, Settings
from .auth import sdk_env

if TYPE_CHECKING:
    from .session import Session

log = logging.getLogger(__name__)

TOOL_NAMES = ["dispatch_task", "task_status", "cancel_task", "answer_worker", "approve",
              "set_project", "end_conversation", "ignore"]


@dataclass
class TurnFlags:
    ignored: bool = False
    end_conversation: bool = False
    dispatched: list[str] = field(default_factory=list)


def _text(s: str) -> dict:
    return {"content": [{"type": "text", "text": s}]}


def front_prompt(cfg: Settings) -> str:
    tpl = resources.files("jarvis.prompts").joinpath("front.md").read_text()
    return tpl.format(name=cfg.assistant_name, user=cfg.user_name)


class FrontBrain:
    def __init__(self, settings: Settings, session: Session):
        self.settings = settings
        self.cfg: FrontConfig = settings.front
        self.session = session
        self.flags = TurnFlags()
        self.allow_ignore = False  # só falas incertas podem ser ignoradas
        self._client = None
        self._lock = asyncio.Lock()
        self._turns = 0
        self._history: list[tuple[str, str]] = []  # (usuário, assistente), para reciclar a sessão
        self._cwd = tempfile.mkdtemp(prefix="jarvis-front-")  # cwd neutro: sem CLAUDE.md/git no contexto

    # ---- tools -----------------------------------------------------------------------------

    def _server(self):
        from claude_agent_sdk import create_sdk_mcp_server, tool

        s = self.session

        @tool("dispatch_task", "Envia uma tarefa ao back (Sonnet/Opus) para executar. "
              "brief: pedido lapidado e autossuficiente; project: nome falado, vazio = atual, 'none' = sem repo; "
              "tier: default|complex; effort: low|medium|high|xhigh.",
              {"brief": str, "project": str, "tier": str, "effort": str})
        async def dispatch_task(args: dict) -> dict:
            msg, task_id = s.dispatch(args["brief"], args.get("project", ""), args.get("tier", "default"),
                                      args.get("effort", "medium"))
            if task_id:
                self.flags.dispatched.append(task_id)
                msg += " Encerre o turno sem escrever mais nada."
            return _text(msg)

        @tool("task_status", "Status das tarefas. task_id = número da tarefa para o detalhe (fase, tempo, o que "
              "está fazendo, últimos passos, resultado); vazio = resumo de todas.", {"task_id": str})
        async def task_status(args: dict) -> dict:
            tid = (args.get("task_id") or "").strip()
            if tid:
                t = s.tasks.get(tid)
                return _text(t.detail() if t else f"Não existe tarefa #{tid.lstrip('#')}. {s.tasks_summary()}")
            return _text(s.tasks_summary() or "Nenhuma tarefa.")

        @tool("cancel_task", "Cancela uma tarefa pelo id.", {"task_id": str})
        async def cancel_task(args: dict) -> dict:
            return _text(s.tasks.cancel(args["task_id"]))

        @tool("answer_worker", "Responde a pergunta pendente de uma tarefa. task_id vazio = a única pendente.",
              {"task_id": str, "answer": str})
        async def answer_worker(args: dict) -> dict:
            return _text(s.tasks.answer(args.get("task_id") or None, args["answer"]))

        @tool("approve", "Aprova (true) ou nega (false) a ação pendente de uma tarefa.",
              {"task_id": str, "approved": bool})
        async def approve(args: dict) -> dict:
            return _text(s.tasks.approve(args.get("task_id") or None, bool(args["approved"])))

        @tool("set_project", "Define o projeto padrão pelo nome falado.", {"project": str})
        async def set_project(args: dict) -> dict:
            return _text(s.set_project(args["project"]))

        @tool("end_conversation", "Encerra a conversa (o mestre terminou). Tarefas seguem em background.", {})
        async def end_conversation(args: dict) -> dict:
            self.flags.end_conversation = True
            return _text("Conversa será encerrada após sua despedida.")

        @tool("ignore", "A fala não era dirigida a você: não responda.", {})
        async def ignore(args: dict) -> dict:
            if not self.allow_ignore:
                return _text("Esta fala É para você (veio após a wake-word ou é resposta direta). Responda normalmente.")
            self.flags.ignored = True
            return _text("Ignorado. Não escreva nada.")

        return create_sdk_mcp_server(name="jarvis", version="2.0.0", tools=[
            dispatch_task, task_status, cancel_task, answer_worker, approve, set_project, end_conversation, ignore,
        ])

    # ---- sessão ----------------------------------------------------------------------------

    def _options(self, extra_system: str = ""):
        from claude_agent_sdk import ClaudeAgentOptions

        return ClaudeAgentOptions(
            model=self.cfg.model,
            system_prompt=front_prompt(self.settings) + extra_system,
            tools=[],
            allowed_tools=[f"mcp__jarvis__{n}" for n in TOOL_NAMES],
            mcp_servers={"jarvis": self._server()},
            setting_sources=[],
            include_partial_messages=True,
            max_thinking_tokens=0,
            cwd=self._cwd,
            env=sdk_env(),
        )

    async def start(self, extra_system: str = "") -> None:
        from claude_agent_sdk import ClaudeSDKClient

        self._client = ClaudeSDKClient(options=self._options(extra_system))
        await self._client.connect()
        self._turns = 0
        log.info("front (%s) conectado", self.cfg.model)

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None

    async def _recycle(self) -> None:
        recent = self._history[-6:]
        memory = "\n".join(f"Mestre: {u}\nVocê: {a}" for u, a in recent)
        await self.stop()
        await self.start(f"\n\nTrecho recente da conversa (para contexto):\n{memory}" if memory else "")

    async def interrupt(self) -> None:
        if self._client is not None and self._lock.locked():
            try:
                await self._client.interrupt()
            except Exception:
                log.debug("falha ao interromper o front", exc_info=True)

    async def ask(self, message: str, user_text: str = "") -> AsyncIterator[str]:
        """Envia uma mensagem e devolve os deltas de texto da resposta (streaming).

        A resposta é produzida numa task própria: se quem consome for cancelado (barge-in), a resposta
        continua sendo drenada até o fim, de modo que tools já chamadas terminem e a próxima query
        comece limpa.
        """
        q: asyncio.Queue[str | None] = asyncio.Queue()
        await self._lock.acquire()
        asyncio.create_task(self._produce(message, user_text, q))
        while (item := await q.get()) is not None:
            yield item

    async def _produce(self, message: str, user_text: str, q: asyncio.Queue) -> None:
        from claude_agent_sdk.types import AssistantMessage, ResultMessage, StreamEvent, ToolUseBlock

        reply: list[str] = []
        try:
            if self._client is None:
                await self.start()
            elif self._turns >= self.cfg.recycle_turns:
                await self._recycle()
            self.flags = TurnFlags()
            self._turns += 1
            await self._client.query(message)
            async for msg in self._client.receive_response():
                if isinstance(msg, StreamEvent):
                    ev: dict[str, Any] = msg.event
                    if ev.get("type") == "content_block_delta" and ev["delta"].get("type") == "text_delta":
                        # Depois de um dispatch bem-sucedido a confirmação já foi dita: o resto é
                        # descartado (o modelo tende a anunciar "pronto" antes da hora).
                        if not self.flags.ignored and not self.flags.dispatched:
                            reply.append(ev["delta"]["text"])
                            q.put_nowait(ev["delta"]["text"])
                    elif ev.get("type") == "content_block_stop":
                        q.put_nowait("\n")  # fim de bloco: fala o que ficou pendente antes de uma tool
                elif isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, ToolUseBlock):
                            log.info("🔧 front: %s %s", block.name.removeprefix("mcp__jarvis__"),
                                     json.dumps(block.input, ensure_ascii=False)[:300])
                elif isinstance(msg, ResultMessage) and msg.is_error:
                    log.error("front: erro %s", msg.result)
        except Exception:
            log.exception("front: falha no turno")
            await self.stop()  # a próxima chamada reconecta
        finally:
            self._history.append((user_text or message, "".join(reply)))
            self._history = self._history[-20:]
            q.put_nowait(None)
            self._lock.release()
