"""Espera de frase incompleta e continuação após interrupção (com STT/TTS/front falsos)."""

import asyncio

import numpy as np

from jarvis.config import Settings
from jarvis.core import session as session_mod
from jarvis.core.session import Session
from jarvis.protocol import SpeechEnd, SpeechStart


class FakeStt:
    class cfg:
        min_chunk_s = 99

    def __init__(self, texts):
        self.texts = list(texts)

    def transcribe(self, pcm):
        return self.texts.pop(0)


class FakeTts:
    sample_rate = 24000

    def synth(self, text):
        return np.zeros(10, dtype=np.int16)


class FakeFront:
    def __init__(self, delay=0.0):
        self.messages = []
        self.delay = delay
        from jarvis.core.front import TurnFlags

        self.flags = TurnFlags()

    async def ask(self, message, user_text=""):
        self.messages.append(message)
        await asyncio.sleep(self.delay)
        yield "Ok."

    async def interrupt(self):
        pass


async def _speak(sess, after_wake=False):
    await sess.handle(SpeechStart(after_wake=after_wake))
    sess.handle_audio(b"\x00\x00" * 16000)
    await sess.handle(SpeechEnd(duration_ms=10))


def _session(texts, front, tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    monkeypatch.setattr(session_mod, "INCOMPLETE_GRACE_S", 0.3)
    s = Settings(projects_root=[tmp_path])
    return Session(s, FakeStt(texts), FakeTts(), ThreadPoolExecutor(2), front=front)


def test_incomplete_phrase_waits_and_merges(tmp_path, monkeypatch):
    async def run():
        front = FakeFront()
        sess = _session(["lista os arquivos do projeto e", "me diz qual é o maior"], front, tmp_path, monkeypatch)
        await _speak(sess, after_wake=True)
        await asyncio.sleep(0.1)
        assert front.messages == []  # segurou a frase incompleta
        await _speak(sess)
        await asyncio.sleep(0.2)
        return front.messages

    msgs = asyncio.run(run())
    assert len(msgs) == 1
    assert "lista os arquivos do projeto e me diz qual é o maior" in msgs[0]


def test_incomplete_phrase_released_after_grace(tmp_path, monkeypatch):
    async def run():
        front = FakeFront()
        sess = _session(["abre o navegador e"], front, tmp_path, monkeypatch)
        await _speak(sess, after_wake=True)
        await asyncio.sleep(0.6)
        return front.messages

    msgs = asyncio.run(run())
    assert len(msgs) == 1 and "abre o navegador e" in msgs[0]


def test_speaking_again_while_thinking_becomes_continuation(tmp_path, monkeypatch):
    async def run():
        front = FakeFront(delay=1.0)  # front "pensando"
        sess = _session(["cria um arquivo notas", "com a data de hoje"], front, tmp_path, monkeypatch)
        await _speak(sess, after_wake=True)
        await asyncio.sleep(0.2)
        assert sess.state == "thinking"
        await _speak(sess)
        await asyncio.sleep(0.2)
        return front.messages

    msgs = asyncio.run(run())
    assert len(msgs) == 2
    assert "continuação" in msgs[1] and "com a data de hoje" in msgs[1]


def test_ignored_speech_gives_context_and_no_beep(tmp_path, monkeypatch):
    from jarvis.protocol import Earcon

    sent = []

    class IgnoringFront(FakeFront):
        async def ask(self, message, user_text=""):
            self.messages.append(message)
            self.flags.ignored = True
            if False:
                yield ""

    async def run():
        front = IgnoringFront()
        sess = _session(["ele falou que o deploy quebrou ontem", "e ninguém viu isso até agora"], front,
                        tmp_path, monkeypatch)

        async def capture(msg):
            sent.append(msg)

        sess.send = capture
        sess.open = True
        sess.last_assistant_end = __import__("time").monotonic() - 60
        await _speak(sess)
        await asyncio.sleep(0.8)  # 1ª fala termina em "ontem": sem espera; vai ambígua e é ignorada
        await _speak(sess)
        await asyncio.sleep(0.8)
        return front.messages

    msgs = asyncio.run(run())
    # a 1ª ("ele falou...") é descartada já no filtro local; mesmo assim vira contexto da 2ª
    assert "[endereçamento incerto]" in msgs[-1]
    assert "NÃO eram para você" in msgs[-1] and "deploy quebrou" in msgs[-1]
    assert not any(isinstance(m, Earcon) and m.name == "ack" for m in sent)
