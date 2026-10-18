"""Sessão de conversa: liga edge, STT, endereçamento, front (Haiku), fala e tarefas.

Estados: idle (só wake-word) → listening (janela aberta) → thinking → speaking → listening ...
A janela fecha com `end_conversation`, pela bandeja ou após `window_s` de silêncio. Tarefas seguem
em background e, ao terminar, o assistente avisa: earcon, frase curta e notificação, reabrindo
a janela por alguns segundos.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from collections import deque
from concurrent.futures import Executor
from datetime import datetime
from typing import Literal

from ..config import DATA_DIR, Settings
from ..protocol import (Earcon, Hello, Listen, Notify, PlaybackDone, SpeechCancel, SpeechEnd, SpeechPause,
                        SpeechStart, State, StopAudio, TaskInfo, UserCommand, Wake, dump)
from .addressing import AddressContext, decide, normalize
from .front import FrontBrain
from .projects import ProjectRegistry
from .speech_out import SpeechOut
from .stt import Stt, Utterance
from .tasks import EventKind, Task, TaskManager
from .tts import Tts

log = logging.getLogger(__name__)

StateName = Literal["idle", "listening", "thinking", "speaking"]
PLAYBACK_TIMEOUT_S = 90.0
# Fala que termina num conectivo/muleta provavelmente não acabou: espera a continuação.
INCOMPLETE_GRACE_S = 1.2
_INCOMPLETE_END = re.compile(
    r"\b(e|ou|mas|de|do|da|dos|das|que|para|pra|pro|com|em|no|na|nos|nas|o|a|os|as|um|uma|uns|umas|"
    r"tipo|entao|porque|se|quando|como|onde|pelo|pela|ao|aos|meu|minha|seu|sua|esse|essa|este|esta|"
    r"eh|hum|hmm|sobre|sem|ate|aquele|aquela)$"
)
# Falas ignoradas (não eram para o assistente) ficam como contexto por este tempo.
IGNORED_CONTEXT_S = 20.0
# Nova fala logo após uma resposta interrompida = continuação da anterior.
CONTINUATION_GAP_S = 4.0
_WEEKDAYS = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]


class Session:
    def __init__(self, settings: Settings, stt: Stt, tts: Tts, executor: Executor,
                 task_manager: TaskManager | None = None, front: FrontBrain | None = None):
        self.settings = settings
        self.stt = stt
        self.executor = executor
        self.link = None
        self.state: StateName = "idle"
        self.open = False
        self.utt: Utterance | None = None
        self._utt_flags = (False, False)  # (after_wake, barge_in) da fala em andamento
        self.turn_task: asyncio.Task | None = None
        self.current_utt: str | None = None
        self._playback_done: dict[str, asyncio.Event] = {}
        self.last_assistant_end = 0.0
        self.assistant_asked = False
        self._timer: asyncio.TimerHandle | None = None
        self.events: list[str] = []
        self._held: tuple[str, bool, bool, float] | None = None  # fala incompleta à espera
        self._held_timer: asyncio.TimerHandle | None = None
        self._last_sent_end = 0.0  # fim (perf_counter) da última fala enviada ao front
        self._last_turn_interrupted = False
        self._ignored: deque[tuple[float, str]] = deque(maxlen=5)  # (monotonic, texto)
        self._announcements: asyncio.Queue[tuple[str, bool]] = asyncio.Queue()
        self.projects = ProjectRegistry(settings.projects_root)
        self.tasks = task_manager or TaskManager(settings.back, self._on_task_event, start_gate=self._front_quiet)
        self.front = front or FrontBrain(settings, self)
        self.speech = SpeechOut(tts, executor, self.send, self.send_bytes, settings.front.max_sentences)
        self._announcer = asyncio.create_task(self._announce_loop())

    # ---- link com o edge -------------------------------------------------------------------

    async def send(self, msg) -> None:
        if isinstance(msg, Notify):
            log.info("🔔 %s%s", msg.title, "" if self.link else " (sem edge conectado)")
        if self.link is not None:
            try:
                await self.link.send(dump(msg))
            except Exception:
                log.debug("falha ao enviar ao edge", exc_info=True)

    async def send_bytes(self, data: bytes) -> None:
        if self.link is not None:
            try:
                await self.link.send(data)
            except Exception:
                log.debug("falha ao enviar áudio ao edge", exc_info=True)

    async def attach(self, ws) -> None:
        self.link = ws

    async def detach(self, ws) -> None:
        if self.link is ws:
            self.link = None
            await self._cancel_turn()
            self.open = False
            self._cancel_timer()
            self.state = "idle"

    async def push_state(self) -> None:
        tasks = [TaskInfo(id=t.id, title=t.title, status=t.status) for t in self.tasks.recent()]
        await self.send(State(state=self.state, conversation_open=self.open, tasks=tasks))

    async def set_state(self, state: StateName) -> None:
        if state != self.state:
            self.state = state
            await self.push_state()

    # ---- janela de conversa ----------------------------------------------------------------

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _restart_timer(self, timeout: float | None = None) -> None:
        self._cancel_timer()
        if self.open:
            loop = asyncio.get_running_loop()
            self._timer = loop.call_later(timeout or self.settings.front.window_s,
                                          lambda: asyncio.ensure_future(self.close_window()))

    async def open_window(self, timeout: float | None = None) -> None:
        self.open = True
        await self.send(Listen(open=True, timeout_s=timeout or self.settings.front.window_s))
        self._restart_timer(timeout)
        if self.state == "idle":
            await self.set_state("listening")

    async def close_window(self) -> None:
        if not self.open:
            return
        if self.utt is not None or self._busy_turn():
            self._restart_timer()  # ainda falando/pensando: adia
            return
        self.open = False
        self._cancel_timer()
        await self.send(Listen(open=False))
        await self.send(Earcon(name="close"))
        await self.set_state("idle")
        log.info("conversa encerrada")

    # ---- eventos do edge -------------------------------------------------------------------

    async def handle(self, msg) -> None:
        match msg:
            case Hello():
                log.info("edge conectado (%s %s)", msg.platform, msg.version)
                await self.send(Listen(open=self.open))
                await self.push_state()
            case Wake():
                await self._cancel_turn()
                await self.open_window()
                await self.set_state("listening")
            case SpeechStart():
                self._cancel_timer()
                if self._held_timer is not None:  # continuou a frase incompleta
                    self._held_timer.cancel()
                    self._held_timer = None
                if msg.barge_in or msg.after_wake or (self._busy_turn() and self.state == "thinking"):
                    await self._cancel_turn()
                if self.utt:
                    self.utt.cancel()
                # Corta o silêncio final (menos uma folga de 350 ms para não comer a última sílaba).
                self.utt = Utterance(self.stt, self.executor,
                                     trim_tail_ms=max(0, self.settings.vad.end_silence_ms - 350))
                self._utt_flags = (msg.after_wake, msg.barge_in)
                await self.set_state("listening")
            case SpeechPause():
                if self.utt:
                    self.utt.pause()
            case SpeechEnd():
                utt, self.utt = self.utt, None
                if utt:
                    asyncio.create_task(self._on_utterance(utt, *self._utt_flags))
            case SpeechCancel():
                log.info("🎙 fala descartada pelo edge: %s", msg.reason or "?")
                if self.utt:
                    self.utt.cancel()
                self.utt = None
                self._restart_timer()
            case PlaybackDone():
                ev = self._playback_done.get(msg.utterance_id)
                if ev:
                    ev.set()
            case UserCommand():
                if msg.command == "end_conversation":
                    await self._cancel_turn()
                    await self.close_window()

    def handle_audio(self, data: bytes) -> None:
        if self.utt is not None:
            self.utt.add(data)

    # ---- turno de conversa -----------------------------------------------------------------

    def _busy_turn(self) -> bool:
        return self.turn_task is not None and not self.turn_task.done()

    async def _cancel_turn(self) -> None:
        if self._busy_turn():
            self._last_turn_interrupted = True
            self.turn_task.cancel()
            await self.front.interrupt()
        if self.current_utt:
            await self.send(StopAudio())
            self.current_utt = None

    async def _on_utterance(self, utt: Utterance, after_wake: bool, barge_in: bool) -> None:
        t_end = time.perf_counter()
        text = await utt.finish()
        t_stt = time.perf_counter()
        if os.environ.get("JARVIS_DEBUG_AUDIO"):
            path = utt.save_debug(DATA_DIR / "debug", text)
            log.info("💾 áudio da fala salvo em %s", path)
        if self._held is not None:  # junta com o começo da frase que estava à espera
            prev, prev_wake, prev_barge, _ = self._held
            self._held = None
            text = f"{prev} {text}".strip()
            after_wake, barge_in = after_wake or prev_wake, barge_in or prev_barge
        if not text:
            self._restart_timer()
            return
        continuation = self._last_turn_interrupted and utt.started - self._last_sent_end < CONTINUATION_GAP_S
        if not continuation and _INCOMPLETE_END.search(normalize(text)):
            log.info("🎤 %r … (parece incompleta, aguardando %.1fs)", text, INCOMPLETE_GRACE_S)
            self._held = (text, after_wake, barge_in, t_end)
            self._held_timer = asyncio.get_running_loop().call_later(
                INCOMPLETE_GRACE_S, lambda: asyncio.ensure_future(self._release_held()))
            return
        await self._handle_text(text, after_wake, barge_in, t_end, continuation, t_stt - t_end)

    async def _release_held(self) -> None:
        """Ninguém continuou a frase: segue com o que foi dito."""
        self._held_timer = None
        if self._held is None or self.utt is not None:
            return
        text, after_wake, barge_in, t_end = self._held
        self._held = None
        await self._handle_text(text, after_wake, barge_in, t_end, False, 0.0)

    async def _handle_text(self, text: str, after_wake: bool, barge_in: bool, t_end: float,
                           continuation: bool, stt_s: float) -> None:
        since = time.monotonic() - self.last_assistant_end if self.last_assistant_end else 1e9
        ctx = AddressContext(after_wake=after_wake, barge_in=barge_in, since_assistant_s=since,
                             assistant_asked=self.assistant_asked, pending_request=bool(self.tasks.pending()),
                             after_ignored=bool(self._recent_ignored()), name=self.settings.assistant_name)
        decision = "accept" if continuation else decide(text, ctx)
        log.info("🎤 %r → %s%s (stt %.0f ms)", text, decision, " [continuação]" if continuation else "",
                 stt_s * 1000)
        if decision == "reject":
            self._ignored.append((time.monotonic(), text))
            self._restart_timer()
            if not self._busy_turn():
                await self.set_state("listening" if self.open else "idle")
            return
        await self._cancel_turn()
        self._last_sent_end = t_end
        self._last_turn_interrupted = False
        self.turn_task = asyncio.create_task(self._turn(text, decision == "ambiguous", t_end, continuation))

    def _recent_ignored(self) -> list[tuple[float, str]]:
        now = time.monotonic()
        return [(t, x) for t, x in self._ignored if now - t < IGNORED_CONTEXT_S]

    def compose(self, text: str, ambiguous: bool = False, continuation: bool = False) -> str:
        cur = self.projects.current
        now = datetime.now().astimezone()
        parts = [f"agora: {_WEEKDAYS[now.weekday()]} {now:%d/%m/%Y %H:%M} (UTC{now:%z})",
                 f"projeto atual: {cur.name if cur else 'nenhum'}",
                 f"projetos conhecidos: {', '.join(self.projects.names())}"]
        active = self.tasks.active()
        if active:
            parts.append("tarefas: " + "; ".join(t.line() for t in active))
        if self.events:
            parts.append("eventos novos: " + " | ".join(self.events))
            self.events.clear()
        ignored = self._recent_ignored()
        if ignored:
            now = time.monotonic()
            parts.append("falas recentes que NÃO eram para você: "
                         + "; ".join(f'"{x}" (há {now - t:.0f}s)' for t, x in ignored))
        header = "[contexto] " + " · ".join(parts)
        if ambiguous:
            header += "\n[endereçamento incerto]"
        if continuation:
            return (f"{header}\nMestre (continuação da fala anterior, que você não chegou a responder; "
                    f"considere as duas juntas e não repita ações já feitas): {text}")
        return f"{header}\nMestre: {text}"

    async def _turn(self, text: str, ambiguous: bool, t_end: float, continuation: bool = False) -> None:
        if not ambiguous:  # na dúvida, silêncio total: nada de bip se ele acabar ignorando
            await self.send(Earcon(name="ack"))
        await self.set_state("thinking")
        utt_ready: list[str] = []

        def first_audio(utt: str) -> None:
            utt_ready.append(utt)
            self.current_utt = utt
            self._playback_done[utt] = asyncio.Event()
            log.info("⏱ fim da fala → 1º áudio: %.0f ms", (time.perf_counter() - t_end) * 1000)
            asyncio.ensure_future(self.set_state("speaking"))

        self.speech.on_first_audio = first_audio
        self.front.allow_ignore = ambiguous
        utt, spoken = await self.speech.speak_stream(self.front.ask(self.compose(text, ambiguous, continuation), text))
        flags = self.front.flags
        if flags.ignored:
            self._ignored.append((time.monotonic(), text))
            log.info("🙈 ignorada: %r", text)
        if not spoken and flags.dispatched:  # despachou sem confirmar em voz
            spoken = f"Deixa comigo, {self.settings.user_name}."
            utt = await self.speech.say(spoken)
        if spoken:
            log.info("🗣 %s", spoken)
            self.assistant_asked = spoken.rstrip().endswith("?")
        await self._finish_speech(utt, close=flags.end_conversation)

    async def _finish_speech(self, utt: str | None, close: bool = False) -> None:
        """Espera o edge terminar de tocar e decide: fecha a janela ou reabre o timer."""
        if utt is not None:
            ev = self._playback_done.get(utt)
            try:
                if ev:
                    await asyncio.wait_for(ev.wait(), PLAYBACK_TIMEOUT_S)
            except asyncio.TimeoutError:
                log.warning("playback_done não chegou para %s", utt)
            finally:
                self._playback_done.pop(utt, None)
                if self.current_utt == utt:
                    self.current_utt = None
            self.last_assistant_end = time.monotonic()
        if close:
            self.turn_task = None
            await self.close_window()
        else:
            await self.set_state("listening" if self.open else "idle")
            self._restart_timer()

    async def _front_quiet(self, timeout: float = 10.0) -> None:
        """Espera o turno atual (fala do front) terminar antes de subir um worker."""
        deadline = time.monotonic() + timeout
        await asyncio.sleep(0.2)
        while (self._busy_turn() or self.current_utt) and time.monotonic() < deadline:
            await asyncio.sleep(0.2)

    # ---- ações chamadas pelas tools do front ------------------------------------------------

    def dispatch(self, brief: str, project: str, tier: str, effort: str) -> tuple[str, str | None]:
        project = (project or "").strip()
        if project.lower() in ("none", "nenhum"):
            path = None
        elif project:
            path = self.projects.resolve(project)
            if path is None:
                names = ", ".join(self.projects.names()[:15])
                return f"Projeto '{project}' não encontrado. Conhecidos: {names}.", None
            self.projects.set_current(path)
        else:
            path = self.projects.current
        task = self.tasks.dispatch(brief, path, tier, effort)
        asyncio.ensure_future(self.push_state())
        where = path.name if path else "sem repositório"
        log.info("📋 tarefa #%s (%s, %s): %s", task.id, task.model_label, where, brief)
        return f"Tarefa #{task.id} despachada ({task.model_label}, {where}).", task.id

    def set_project(self, name: str) -> str:
        path = self.projects.resolve(name)
        if path is None:
            return f"Projeto '{name}' não encontrado. Conhecidos: {', '.join(self.projects.names()[:15])}."
        self.projects.set_current(path)
        return f"Projeto atual: {path.name}."

    def tasks_summary(self) -> str:
        lines = []
        for t in self.tasks.recent():
            line = t.line()
            if t.activity and t.status in ("planejando", "executando"):
                line += f" — agora: {t.activity}"
            lines.append(line)
        return "\n".join(lines)

    # ---- eventos das tarefas → voz ----------------------------------------------------------

    async def _on_task_event(self, task: Task, kind: EventKind) -> None:
        await self.push_state()
        icon = {"started": "▶", "cancelled": "⏹", "done": "✅", "failed": "❌", "question": "❓", "approval": "🔐"}[kind]
        detail = task.pending.text if task.pending else task.spoken_summary
        log.info("%s tarefa #%s %s%s", icon, task.id, kind, f": {detail}" if detail else "")
        if kind in ("started", "cancelled"):
            return
        if kind in ("done", "failed"):
            verb = "concluída" if kind == "done" else "falhou"
            self.events.append(f"#{task.id} {verb}: {task.spoken_summary}")
            body = (task.report or task.spoken_summary).replace("**", "").replace("`", "")
            await self.send(Notify(title=f"Jarvis — tarefa #{task.id} {verb}", body=body[:500]))
            self._announcements.put_nowait((f"Tarefa {task.id} {'pronta' if kind == 'done' else 'falhou'}. "
                                            f"{task.spoken_summary}", False))
        elif kind == "question" and task.pending:
            self.events.append(f"#{task.id} pergunta: {task.pending.text}")
            await self.send(Notify(title=f"Jarvis — tarefa #{task.id} pergunta", body=task.pending.text))
            self._announcements.put_nowait((f"A tarefa {task.id} pergunta: {task.pending.text}", True))
        elif kind == "approval" and task.pending:
            self.events.append(f"#{task.id} pede aprovação: {task.pending.text}")
            await self.send(Notify(title=f"Jarvis — tarefa #{task.id} pede aprovação", body=task.pending.text))
            self._announcements.put_nowait((f"A tarefa {task.id} quer {task.pending.text}. Posso?", True))

    async def _announce_loop(self) -> None:
        while True:
            text, needs_reply = await self._announcements.get()
            while self.utt is not None or self._busy_turn() or self.current_utt:
                await asyncio.sleep(0.3)
            if not self.open:
                await self.send(Earcon(name="done"))
                await self.open_window(self.settings.front.reopen_s * (3 if needs_reply else 1))
            self._cancel_timer()
            self.turn_task = asyncio.create_task(self._announce(text, needs_reply))

    async def _announce(self, text: str, needs_reply: bool) -> None:
        def first_audio(utt: str) -> None:
            self.current_utt = utt
            self._playback_done[utt] = asyncio.Event()
            asyncio.ensure_future(self.set_state("speaking"))

        self.speech.on_first_audio = first_audio
        log.info("📣 %s", text)
        utt = await self.speech.say(text)
        self.assistant_asked = True  # um anúncio convida resposta ("é esse mesmo", "pode ir")
        await self._finish_speech(utt)
        if not needs_reply:
            self._restart_timer(self.settings.front.reopen_s)
