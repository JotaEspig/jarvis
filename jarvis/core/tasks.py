"""Gerência das tarefas do back: várias em paralelo, com perguntas/aprovações mediadas por voz."""

from __future__ import annotations

import asyncio
import itertools
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Literal

from ..config import BackConfig

log = logging.getLogger(__name__)

Status = Literal["na fila", "planejando", "executando", "aguardando você", "concluída", "falhou", "cancelada"]
EventKind = Literal["started", "question", "approval", "done", "failed", "cancelled"]


@dataclass
class PendingRequest:
    kind: Literal["question", "approval"]
    text: str
    future: asyncio.Future


@dataclass
class Task:
    id: str
    title: str
    brief: str
    project: Path | None
    tier: Literal["default", "complex"]
    effort: str
    status: Status = "na fila"
    spoken_summary: str = ""
    report: str = ""
    activity: str = ""
    history: deque = field(default_factory=lambda: deque(maxlen=8))  # (timestamp, passo)
    plan: str = ""
    pending: PendingRequest | None = None
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    handle: asyncio.Task | None = None

    def log_activity(self, text: str) -> None:
        if text and text != self.activity:
            self.activity = text
            self.history.append((time.time(), text))

    def detail(self) -> str:
        """Relatório de status para o front resumir em voz."""
        now = time.time()
        lines = [f"Tarefa #{self.id} ({self.model_label}, projeto {self.project.name if self.project else 'nenhum'}): "
                 f"{self.status}."]
        lines.append(f"Pedido: {self.brief}")
        if self.started:
            lines.append(f"Tempo: {_fmt_duration((self.finished or now) - self.started)}"
                         f"{' no total' if self.finished else ' até agora'}.")
        elif self.status == "na fila":
            lines.append("Ainda na fila (limite de tarefas paralelas).")
        if self.pending:
            kind = "pergunta" if self.pending.kind == "question" else "pede aprovação para"
            lines.append(f"Esperando o mestre: {kind} {self.pending.text}")
        if self.status in ("planejando", "executando", "aguardando você") and self.activity:
            lines.append(f"Fazendo agora: {self.activity}")
        if self.history:
            steps = "; ".join(f"{_fmt_duration(now - t)} atrás: {a}" for t, a in list(self.history)[-5:])
            lines.append(f"Últimos passos: {steps}")
        if self.plan:
            lines.append(f"Plano (resumo): {self.plan[:600]}")
        if self.status in ("concluída", "falhou"):
            lines.append(f"Resultado: {self.spoken_summary}")
            if self.report:
                lines.append(f"Relatório: {self.report[:800]}")
        return "\n".join(lines)

    @property
    def model_label(self) -> str:
        return "Opus" if self.tier == "complex" else "Sonnet"

    def line(self) -> str:
        s = f'#{self.id} {self.status} ({self.model_label}): "{self.title}"'
        if self.pending:
            s += f' — {"pergunta" if self.pending.kind == "question" else "pede aprovação"}: "{self.pending.text}"'
        elif self.status in ("concluída", "falhou") and self.spoken_summary:
            s += f" — {self.spoken_summary}"
        return s


OnEvent = Callable[[Task, EventKind], Awaitable[None]]
Runner = Callable[["Task", "TaskManager"], Awaitable[None]]


def _fmt_duration(sec: float) -> str:
    sec = int(sec)
    if sec < 60:
        return f"{sec}s"
    m, s = divmod(sec, 60)
    return f"{m}min{s:02d}s" if m < 60 else f"{m // 60}h{m % 60:02d}min"


_NUMBER_WORDS = {"um": "1", "uma": "1", "dois": "2", "duas": "2", "tres": "3", "três": "3", "quatro": "4",
                 "cinco": "5", "seis": "6", "sete": "7", "oito": "8", "nove": "9", "dez": "10"}


def normalize_task_id(raw: str) -> str:
    """'#2', 'tarefa 2', 'dois', 'Tarefa Dois' → '2'."""
    t = raw.strip().lower().lstrip("#").removeprefix("tarefa").strip().lstrip("#").strip()
    return _NUMBER_WORDS.get(t, t)


def make_title(brief: str, n: int = 8) -> str:
    words = brief.split()
    return " ".join(words[:n]) + ("…" if len(words) > n else "")


class TaskManager:
    def __init__(self, cfg: BackConfig, on_event: OnEvent, runner: Runner | None = None,
                 start_gate: Callable[[], Awaitable[None]] | None = None):
        self.cfg = cfg
        # Aguardado antes de iniciar o worker (o processo dele disputa CPU com a fala do front).
        self.start_gate = start_gate
        self.on_event = on_event
        self.tasks: dict[str, Task] = {}
        self._ids = itertools.count(1)
        self._sem = asyncio.Semaphore(cfg.max_parallel)
        if runner is None:
            from .worker import run_task

            runner = run_task
        self._runner = runner

    def dispatch(self, brief: str, project: Path | None, tier: str = "default", effort: str = "medium") -> Task:
        tier = "complex" if tier == "complex" else "default"
        task = Task(id=str(next(self._ids)), title=make_title(brief), brief=brief, project=project,
                    tier=tier, effort=effort)  # type: ignore[arg-type]
        self.tasks[task.id] = task
        task.handle = asyncio.create_task(self._run(task), name=f"task-{task.id}")
        return task

    async def _run(self, task: Task) -> None:
        try:
            if self.start_gate is not None:
                await self.start_gate()
            async with self._sem:
                task.started = time.time()
                await self.on_event(task, "started")
                await self._runner(task, self)
            task.status = "concluída"
            await self.on_event(task, "done")
        except asyncio.CancelledError:
            task.status = "cancelada"
            await self.on_event(task, "cancelled")
        except Exception as e:
            log.exception("tarefa #%s falhou", task.id)
            task.status = "falhou"
            task.spoken_summary = task.spoken_summary or f"A tarefa falhou: {str(e)[:120]}"
            await self.on_event(task, "failed")
        finally:
            task.finished = time.time()
            if task.pending and not task.pending.future.done():
                task.pending.future.cancel()
            task.pending = None

    # ---- chamadas do worker --------------------------------------------------------------

    async def request(self, task: Task, kind: Literal["question", "approval"], text: str):
        """Bloqueia o worker até o usuário responder por voz."""
        fut = asyncio.get_running_loop().create_future()
        task.pending = PendingRequest(kind, text, fut)
        prev, task.status = task.status, "aguardando você"
        await self.on_event(task, kind)
        try:
            return await fut
        finally:
            task.pending = None
            task.status = prev

    # ---- chamadas do front ---------------------------------------------------------------

    def get(self, task_id: str | None) -> Task | None:
        if task_id:
            return self.tasks.get(normalize_task_id(task_id))
        pend = self.pending()
        return pend[0] if len(pend) == 1 else None

    def answer(self, task_id: str | None, text: str) -> str:
        t = self.get(task_id)
        if not t or not t.pending or t.pending.kind != "question":
            return "Nenhuma pergunta pendente com esse id."
        t.pending.future.set_result(text)
        return f"Resposta enviada para a tarefa #{t.id}."

    def approve(self, task_id: str | None, ok: bool) -> str:
        t = self.get(task_id)
        if not t or not t.pending or t.pending.kind != "approval":
            return "Nenhuma aprovação pendente com esse id."
        t.pending.future.set_result(ok)
        return f"{'Aprovado' if ok else 'Negado'} para a tarefa #{t.id}."

    def cancel(self, task_id: str) -> str:
        t = self.get(task_id)
        if not t or not t.handle or t.handle.done():
            return "Tarefa não encontrada ou já finalizada."
        t.handle.cancel()
        return f"Cancelando a tarefa #{t.id}."

    def active(self) -> list[Task]:
        return [t for t in self.tasks.values() if t.status not in ("concluída", "falhou", "cancelada")]

    def pending(self) -> list[Task]:
        return [t for t in self.tasks.values() if t.pending is not None]

    def recent(self, limit: int = 6) -> list[Task]:
        return sorted(self.tasks.values(), key=lambda t: t.created, reverse=True)[:limit]
