"""Estado por conversa (uma por conexão WebSocket)."""

from __future__ import annotations

import asyncio
import enum
from dataclasses import dataclass, field

from .jarvis_brain import JarvisBrain


class State(str, enum.Enum):
    INTAKE = "intake"          # conversando com o Jarvis
    HANDOFF = "handoff"        # gerando prompt / iniciando worker
    WORKING = "working"        # worker rodando
    WAITING_USER = "waiting"   # worker aguardando resposta/aprovação do usuário
    DONE = "done"


@dataclass
class Session:
    brain: JarvisBrain = field(default_factory=JarvisBrain)
    state: State = State.INTAKE
    # Future resolvido pela próxima fala/decisão do usuário (ask_user / aprovação).
    pending: asyncio.Future | None = None
    worker_task: asyncio.Task | None = None
    # Acumula o texto do worker para o resumo final por voz.
    worker_text: list[str] = field(default_factory=list)

    def new_pending(self) -> asyncio.Future:
        self.pending = asyncio.get_running_loop().create_future()
        return self.pending

    def resolve_pending(self, value) -> bool:
        if self.pending is not None and not self.pending.done():
            self.pending.set_result(value)
            self.pending = None
            return True
        return False
