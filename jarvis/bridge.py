"""Bridge: máquina de estados que costura voz <-> Jarvis (Haiku) <-> worker.

Uma instância por conexão WebSocket. Envia eventos para a UI via `send` e recebe
mensagens do cliente via `handle`. Voz (STT/TTS) e o Claude Agent SDK são opcionais
em tempo de execução: se não estiverem instalados, o bridge degrada para texto/tela.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any, Awaitable, Callable

from . import worker
from .config import settings
from .session import Session, State

log = logging.getLogger("jarvis.bridge")

Send = Callable[[dict], Awaitable[None]]

_AFFIRM = ("sim", "pode", "autoriz", "aprov", "claro", "ok", "isso", "manda")
_NEGATE = ("não", "nao", "nega", "cancel", "para", "espera", "melhor não")


def _affirmative(text: str) -> bool | None:
    t = text.lower()
    if any(w in t for w in _NEGATE):
        return False
    if any(w in t for w in _AFFIRM):
        return True
    return None


class Bridge:
    def __init__(self, send: Send) -> None:
        self.send = send
        self.s = Session()

    # ---- utilidades de saída -----------------------------------------
    async def _status(self, text: str) -> None:
        await self.send({"type": "status", "state": self.s.state.value, "text": text})

    async def say(self, text: str) -> None:
        """Fala do Jarvis: mostra na tela e (se possível) sintetiza voz por frase."""
        if not text:
            return
        await self.send({"type": "transcript", "role": "jarvis", "text": text})
        try:
            from .voice import synthesize_sentences

            for wav in synthesize_sentences(text):
                b64 = base64.b64encode(wav).decode("ascii")
                await self.send({"type": "tts", "format": "wav", "data": b64})
        except Exception as exc:  # TTS ausente/erro: segue só com texto
            log.debug("TTS indisponível: %s", exc)

    # ---- entrada do cliente ------------------------------------------
    async def handle(self, msg: dict) -> None:
        mtype = msg.get("type")
        if mtype == "control":
            await self._handle_control(msg)
            return
        if mtype == "audio":
            text = await self._transcribe(msg.get("data", ""))
        elif mtype == "text":
            text = (msg.get("text") or "").strip()
        else:
            return
        if not text:
            return
        await self.send({"type": "transcript", "role": "user", "text": text})
        await self._route_utterance(text)

    async def _handle_control(self, msg: dict) -> None:
        action = msg.get("action")
        if action == "end_conversation":
            await self.begin_handoff()
        elif action == "approve":
            self.s.resolve_pending(True)
        elif action == "deny":
            self.s.resolve_pending(False)

    async def _transcribe(self, data_b64: str) -> str:
        if not data_b64:
            return ""
        try:
            from .voice import transcribe

            return transcribe(base64.b64decode(data_b64))
        except Exception as exc:
            log.warning("STT indisponível: %s", exc)
            await self._status("STT indisponível — digite o texto.")
            return ""

    async def _route_utterance(self, text: str) -> None:
        # Se o worker aguarda uma resposta/decisão, esta fala a resolve.
        if self.s.pending is not None and not self.s.pending.done():
            self.s.resolve_pending(text)
            return
        if self.s.state in (State.INTAKE, State.DONE):
            await self.jarvis_turn(text)
        elif self.s.state == State.WORKING:
            await self._status("O worker está trabalhando; aguarde a próxima pergunta.")

    # ---- intake -------------------------------------------------------
    async def jarvis_turn(self, text: str) -> None:
        self.s.state = State.INTAKE
        try:
            reply = await self.s.brain.reply(text)
        except Exception as exc:
            log.exception("Falha no Jarvis (Haiku)")
            await self._status(f"Erro no Jarvis: {exc}")
            return
        await self.say(reply)

    # ---- handoff + worker --------------------------------------------
    async def begin_handoff(self) -> None:
        if self.s.state == State.WORKING:
            return
        self.s.state = State.HANDOFF
        await self._status("Gerando o prompt e escolhendo o modelo…")
        try:
            handoff = await self.s.brain.generate_handoff()
        except Exception as exc:
            log.exception("Falha ao gerar handoff")
            await self._status(f"Erro ao gerar o prompt: {exc}")
            self.s.state = State.INTAKE
            return
        await self.send(
            {
                "type": "handoff",
                "title": handoff.title,
                "prompt": handoff.prompt,
                "model": handoff.model,
                "effort": handoff.effort,
            }
        )
        await self.say(
            f"Vou acionar o agente com o modelo {handoff.model}, esforço {handoff.effort}."
        )
        self.s.worker_text = []
        self.s.state = State.WORKING
        self.s.worker_task = asyncio.create_task(self._run_worker(handoff))

    async def _run_worker(self, handoff) -> None:
        try:
            await worker.run_task(
                handoff,
                ask_user_cb=self._ask_user,
                approval_cb=self._approval,
                on_message=self._on_worker_message,
            )
        except Exception as exc:
            log.exception("Falha no worker")
            await self._status(f"Erro no worker: {exc}")
            self.s.state = State.INTAKE
            return
        await self._finish_worker()

    async def _finish_worker(self) -> None:
        full = "\n".join(self.s.worker_text).strip()
        summary = ""
        if full:
            try:
                summary = await self.s.brain.summarize_for_voice(full)
            except Exception:
                summary = "O agente concluiu a tarefa."
        await self.say(summary or "O agente concluiu a tarefa.")
        self.s.state = State.DONE
        # Sonda até o fim, como no intake.
        await self.say("Isso é tudo, ou quer que eu inicie outra tarefa?")
        await self._status("Concluído.")

    # ---- callbacks da ponte (chamados de dentro do worker) -----------
    async def _wait_user(self) -> str:
        self.s.state = State.WAITING_USER
        fut = self.s.new_pending()
        value = await fut
        self.s.state = State.WORKING
        return value if isinstance(value, str) else ("sim" if value else "não")

    async def _ask_user(self, question: str) -> str:
        spoken = await self.s.brain.phrase_question(question)
        await self.send({"type": "question", "text": spoken})
        await self.say(spoken)
        while True:
            answer = await self._wait_user()
            interp = await self.s.brain.interpret_answer(question, answer)
            if interp.needs_followup and interp.followup_question:
                await self.say(interp.followup_question)
                continue
            return interp.answer

    async def _approval(self, tool_name: str, input_data: dict) -> bool:
        detail = str(input_data.get("command") or input_data.get("path") or "")
        prompt = f"O agente quer usar {tool_name}: {detail}. Posso autorizar?"
        await self.send(
            {"type": "approval", "tool": tool_name, "detail": detail, "text": prompt}
        )
        await self.say(prompt)
        while True:
            fut = self.s.new_pending()
            self.s.state = State.WAITING_USER
            value = await fut
            self.s.state = State.WORKING
            if isinstance(value, bool):
                return value
            decision = _affirmative(value)
            if decision is None:
                await self.say("Não entendi. Pode autorizar? Diga sim ou não.")
                continue
            return decision

    async def _on_worker_message(self, message: Any) -> None:
        for ev in _format_worker_message(message):
            if ev["kind"] == "text":
                self.s.worker_text.append(ev["text"])
            await self.send({"type": "worker", **ev})


def _short(value: Any, limit: int = 200) -> str:
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "…"


def _format_worker_message(message: Any) -> list[dict]:
    """Extrai eventos {kind, text} de uma Message do Claude Agent SDK (duck typing)."""
    events: list[dict] = []
    content = getattr(message, "content", None)
    if not content:
        return events
    for block in content:
        if hasattr(block, "text") and getattr(block, "text"):
            events.append({"kind": "text", "text": block.text})
        elif hasattr(block, "name"):  # ToolUseBlock
            events.append(
                {"kind": "tool", "text": f"{block.name}({_short(getattr(block, 'input', ''))})"}
            )
    return events
