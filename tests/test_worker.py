"""Testa as partes puras do worker sem exigir o Claude Agent SDK."""

import asyncio

from jarvis import worker


def test_needs_approval_gates_risky_tools():
    async def deny(_name, _input):
        return False

    async def allow(_name, _input):
        return True

    # Ferramenta arriscada consulta o callback.
    assert asyncio.run(worker._needs_approval("Bash", deny, {"command": "ls"})) is False
    assert asyncio.run(worker._needs_approval("Bash", allow, {"command": "ls"})) is True
    # Ferramenta de leitura é auto-aprovada (callback nem é chamado).
    assert asyncio.run(worker._needs_approval("Read", deny, {"path": "x"})) is True


def test_ask_user_impl_uses_contextvar_callback():
    async def cb(question: str) -> str:
        return f"resposta para: {question}"

    async def run():
        worker._ask_user_cb.set(cb)
        return await worker._ask_user_impl({"question": "Qual porta usar?"})

    out = asyncio.run(run())
    assert out == {
        "content": [{"type": "text", "text": "resposta para: Qual porta usar?"}]
    }
