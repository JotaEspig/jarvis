"""Testa o handoff do Jarvis (modelo `anthropic` mockado) e o mapeamento de complexidade."""

import asyncio
import base64
import json

from jarvis import jarvis_brain
from jarvis.config import settings
from jarvis.jarvis_brain import Handoff, JarvisBrain, _attachment_block


class _Block:
    type = "text"

    def __init__(self, text: str):
        self.text = text


class _Resp:
    def __init__(self, text: str):
        self.content = [_Block(text)]


class _Messages:
    def __init__(self, text: str):
        self._text = text

    async def create(self, **kwargs):
        return _Resp(self._text)


class _Client:
    def __init__(self, text: str):
        self.messages = _Messages(text)


def _patch_client(monkeypatch, payload: dict):
    monkeypatch.setattr(
        jarvis_brain, "_client", lambda: _Client(json.dumps(payload))
    )


def test_complexity_mapping():
    h = Handoff(title="t", prompt="p", complexity="complex", rationale="r")
    assert h.model == settings.worker_model_complex
    assert h.effort == "high"

    h2 = Handoff(title="t", prompt="p", complexity="trivial", rationale="r")
    assert h2.model == settings.worker_model_simple
    assert h2.effort == "low"


def test_generate_handoff(monkeypatch):
    _patch_client(
        monkeypatch,
        {
            "title": "CLI de data",
            "prompt": "Crie hello.py que imprime a data atual.",
            "complexity": "architectural",
            "rationale": "mudança ampla",
        },
    )
    brain = JarvisBrain()
    brain.history = [{"role": "user", "content": "quero um cli"}]
    h = asyncio.run(brain.generate_handoff())
    assert h.title == "CLI de data"
    assert h.model == settings.worker_model_complex  # architectural -> opus
    assert h.effort == "xhigh"


def test_attachment_block_types():
    # Imagem -> bloco image
    img = _attachment_block({"kind": "image", "media_type": "image/png", "data": "AA=="})
    assert img["type"] == "image" and img["source"]["media_type"] == "image/png"

    # PDF -> bloco document
    pdf = _attachment_block({"kind": "file", "media_type": "application/pdf", "data": "AA=="})
    assert pdf["type"] == "document" and pdf["source"]["media_type"] == "application/pdf"

    # Texto -> bloco text com o conteúdo embutido
    data = base64.b64encode("print('oi')".encode()).decode()
    txt = _attachment_block({"kind": "file", "media_type": "text/x-python", "data": data, "name": "a.py"})
    assert txt["type"] == "text" and "print('oi')" in txt["text"] and "a.py" in txt["text"]

    # Binário desconhecido -> nota textual (não quebra)
    bin_ = _attachment_block({"kind": "file", "media_type": "application/octet-stream", "data": "//80", "name": "x.bin"})
    assert bin_["type"] == "text" and "x.bin" in bin_["text"]


def test_interpret_answer(monkeypatch):
    _patch_client(
        monkeypatch,
        {"answer": "usar SQLite", "needs_followup": False, "followup_question": None},
    )
    brain = JarvisBrain()
    res = asyncio.run(brain.interpret_answer("Qual banco?", "ah, pode ser sqlite mesmo"))
    assert res.answer == "usar SQLite"
    assert res.needs_followup is False
    assert res.followup_question is None
