"""Recuperação de erros do front (cliente do SDK falso)."""

import asyncio

from claude_agent_sdk.types import ResultMessage, StreamEvent

from jarvis.config import Settings
from jarvis.core.front import FrontBrain


def _err(reason=None, status=None):
    return ResultMessage(subtype="success", duration_ms=0, duration_api_ms=0, is_error=True, num_turns=1,
                         session_id="s", terminal_reason=reason, api_error_status=status)


def _ok():
    return ResultMessage(subtype="success", duration_ms=0, duration_api_ms=0, is_error=False, num_turns=1,
                         session_id="s")


def _text(t):
    return StreamEvent(uuid="u", session_id="s",
                       event={"type": "content_block_delta", "delta": {"type": "text_delta", "text": t}})


class FakeClient:
    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.queries = 0

    async def query(self, message):
        self.queries += 1
        self.current = self.scripts.pop(0)

    async def receive_response(self):
        for m in self.current:
            yield m

    async def interrupt(self):
        pass


def _ask(front, text="oi"):
    async def run():
        return "".join([d async for d in front.ask(text)]).strip()
    return asyncio.run(run())


def _front(scripts):
    f = FrontBrain(Settings(), session=None)
    f._client = FakeClient(scripts)
    return f


def test_unrequested_abort_is_retried():
    f = _front([[_err("aborted_streaming")], [_text("Oi, Jota."), _ok()]])
    assert _ask(f) == "Oi, Jota."
    assert f._client.queries == 2


def test_requested_interrupt_is_not_retried():
    f = _front([[_err("aborted_streaming")]])
    f._interrupted = 1  # o turno 1 foi alvo de um barge-in
    assert _ask(f) == ""
    assert f._client.queries == 1


def test_persistent_api_error_speaks_fallback():
    f = _front([[_err(status=529)], [_err(status=529)]])
    assert "problema" in _ask(f)
