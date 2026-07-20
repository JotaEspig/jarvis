"""Testa a máquina de estados do bridge com Jarvis e worker mockados (sem SDKs)."""

import asyncio

from jarvis import bridge as bridge_mod
from jarvis.bridge import Bridge
from jarvis.jarvis_brain import Handoff, InterpretedAnswer


class FakeBlock:
    def __init__(self, text):
        self.text = text


class FakeMsg:
    def __init__(self, blocks):
        self.content = blocks


class FakeBrain:
    async def reply(self, text, attachments=None):
        return f"Entendi: {text}. Isso é tudo?"

    async def generate_handoff(self):
        return Handoff(
            title="App", prompt="Construa o app.", complexity="simple", rationale="ok"
        )

    async def phrase_question(self, q):
        return q

    async def interpret_answer(self, q, a):
        return InterpretedAnswer(answer=a, needs_followup=False, followup_question=None)

    async def summarize_for_voice(self, text):
        return "Resumo: pronto."


async def _fake_run_task(handoff, *, ask_user_cb, approval_cb, on_message, cwd=None):
    await on_message(FakeMsg([FakeBlock("Começando a tarefa.")]))
    db = await ask_user_cb("Qual banco de dados?")
    await on_message(FakeMsg([FakeBlock(f"Usando {db}.")]))
    ok = await approval_cb("Bash", {"command": "pytest"})
    await on_message(FakeMsg([FakeBlock(f"Aprovacao={ok}")]))


def test_full_flow(monkeypatch):
    monkeypatch.setattr(bridge_mod.worker, "run_task", _fake_run_task)

    sent: list[dict] = []

    async def send(evt):
        sent.append(evt)

    async def scenario():
        b = Bridge(send=send)
        b.s.brain = FakeBrain()

        # 1) Intake
        await b.handle({"type": "text", "text": "quero um app"})

        # 2) Encerrar -> handoff + worker começa (task em background)
        await b.handle({"type": "control", "action": "end_conversation"})
        await asyncio.sleep(0.02)  # worker avança até ask_user

        # 3) Responde a pergunta do worker por voz/texto
        await b.handle({"type": "text", "text": "sqlite"})
        await asyncio.sleep(0.02)  # worker avança até a aprovação

        # 4) Aprova a ação (botão)
        await b.handle({"type": "control", "action": "approve"})

        assert b.s.worker_task is not None
        await b.s.worker_task
        return sent

    events = asyncio.run(scenario())
    types = [e["type"] for e in events]

    # intake produziu fala do Jarvis
    assert any(e["type"] == "transcript" and e["role"] == "jarvis" for e in events)
    # handoff foi anunciado com modelo/effort
    handoff = next(e for e in events if e["type"] == "handoff")
    assert handoff["model"] and handoff["effort"] == "medium"  # simple -> sonnet/medium
    # a pergunta do worker foi enviada
    assert any(e["type"] == "question" for e in events)
    # a resposta "sqlite" chegou ao worker (refletida na saída)
    assert any(e.get("kind") == "text" and "sqlite" in e.get("text", "") for e in events)
    # a aprovação resultou em True
    assert any("Aprovacao=True" in e.get("text", "") for e in events if e["type"] == "worker")
    # resumo final + sondagem
    assert any("Resumo" in e.get("text", "") for e in events if e["type"] == "transcript")
    assert any("Isso é tudo" in e.get("text", "") for e in events if e["type"] == "transcript")
