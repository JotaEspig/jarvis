"""Testa as partes puras do worker sem exigir o Claude Agent SDK."""

import asyncio

from jarvis import worker


def test_permission_gates_writes_and_repo():
    async def deny(_name, _input):
        return False

    async def allow(_name, _input):
        return True

    def perm(tool, allow_writes, cb, inp=None):
        return asyncio.run(
            worker._permission(tool, inp or {}, allow_writes=allow_writes, approval_cb=cb)
        )

    # Sem repositório: ferramenta de escrita é negada estruturalmente.
    ok, reason = perm("Write", False, allow)
    assert ok is False and "repositório" in reason

    # Com repositório + aprovação por voz: permitida.
    ok, _ = perm("Write", True, allow)
    assert ok is True

    # Com repositório mas negada por voz.
    ok, reason = perm("Bash", True, deny, {"command": "ls"})
    assert ok is False and "não aprovou" in reason

    # Ferramenta de leitura é sempre liberada (mesmo sem repositório).
    ok, _ = perm("Read", False, deny, {"path": "x"})
    assert ok is True


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
