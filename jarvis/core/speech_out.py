"""Texto do assistente → fala: sanitiza, quebra em frases, corta em N frases e transmite o TTS.

A síntese é encadeada: enquanto uma frase é enviada ao edge, a próxima já está sendo sintetizada.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from concurrent.futures import Executor
from typing import AsyncIterator, Awaitable, Callable

from ..protocol import TtsEnd, TtsStart
from .tts import Tts

log = logging.getLogger(__name__)

_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\U0000FE0F\U0000200D]+"
)
_URL = re.compile(r"https?://\S+")
_CODE_BLOCK = re.compile(r"```.*?(```|$)", re.S)
_MD = re.compile(r"(\*\*|__|\*|`|^#+\s*|^\s*[-•]\s+)", re.M)
# Fim de frase: pontuação seguida de espaço/fim (não quebra "hello.py" nem "3.5").
_SENT_END = re.compile(r"([.!?…]+)(\s+|$)|\n+")
_FILE_DOT = re.compile(r"(?<=\w)\.(?=[A-Za-z]{1,5}\b)")
_FIRST_CLAUSE = re.compile(r"[,;:]\s+")
FIRST_CLAUSE_MIN = 30  # a 1ª frase longa é cortada na 1ª vírgula, para o áudio começar antes


def sanitize(text: str) -> str:
    text = _CODE_BLOCK.sub(" ", text)
    text = _URL.sub("o link", text)
    text = _EMOJI.sub("", text)
    text = _MD.sub("", text)
    text = _FILE_DOT.sub(" ponto ", text)
    return re.sub(r"[ \t]+", " ", text)


class SentenceSplitter:
    """Acumula deltas de texto e devolve frases completas assim que fecham."""

    def __init__(self) -> None:
        self._buf = ""
        self._first = True

    def feed(self, delta: str) -> list[str]:
        self._buf += delta
        out: list[str] = []
        while True:
            m = _SENT_END.search(self._buf)
            # Pontuação colada no fim do buffer ainda é ambígua ("hello." pode virar "hello.py").
            if m and (m.group(1) is None or m.group(2)):
                sent, self._buf = self._buf[:m.end()].strip(), self._buf[m.end():]
                if sent:
                    out.append(sent)
                    self._first = False
                continue
            if self._first and len(self._buf) >= FIRST_CLAUSE_MIN:
                c = _FIRST_CLAUSE.search(self._buf, 15)
                if c:
                    sent, self._buf = self._buf[:c.end()].strip(), self._buf[c.end():]
                    out.append(sent)
                    self._first = False
                    continue
            return out

    def flush(self) -> list[str]:
        rest, self._buf = self._buf.strip(), ""
        return [rest] if rest else []


def count_sentences(text: str) -> int:
    return len([s for s in re.split(r"[.!?…]+(?:\s+|$)", text.strip()) if s.strip()])


async def limit_sentences(deltas: AsyncIterator[str], max_sentences: int) -> AsyncIterator[str]:
    """Quebra o stream em pedaços faláveis e para depois de `max_sentences` frases.

    Pedaços cortados na vírgula (só na 1ª frase) não contam como frase inteira.
    """
    splitter = SentenceSplitter()
    spoken = 0

    def take(pieces: list[str]) -> list[str]:
        nonlocal spoken
        out = []
        for p in pieces:
            p = sanitize(p).strip()
            if not p or spoken >= max_sentences:
                continue
            out.append(p)
            if p[-1] not in ",;:":
                spoken += 1
        return out

    async for d in deltas:
        d = _EMOJI.sub("", d.replace("*", "").replace("`", ""))
        for piece in take(splitter.feed(d)):
            yield piece
        if spoken >= max_sentences:
            # Continua consumindo (para as tools rodarem), sem falar.
            async for _ in deltas:
                pass
            return
    for piece in take(splitter.flush()):
        yield piece


class SpeechOut:
    def __init__(self, tts: Tts, executor: Executor, send_json: Callable[[object], Awaitable[None]],
                 send_bytes: Callable[[bytes], Awaitable[None]], max_sentences: int = 3):
        self._tts = tts
        self._executor = executor
        self._send_json = send_json
        self._send_bytes = send_bytes
        self.max_sentences = max_sentences
        self.on_first_audio: Callable[[str], None] | None = None

    async def speak_stream(self, deltas: AsyncIterator[str]) -> tuple[str | None, str]:
        """Fala o stream. Retorna (utterance_id ou None se nada foi falado, texto falado).

        Produtor (lê o texto e dispara a síntese) e consumidor (envia o áudio em ordem) rodam em
        paralelo: cada frase sai assim que fica pronta, sem esperar a próxima chegar.
        """
        loop = asyncio.get_running_loop()
        t0 = time.perf_counter()
        utt: str | None = None
        spoken: list[str] = []
        queue: asyncio.Queue[tuple[str, asyncio.Future] | None] = asyncio.Queue()

        async def consume() -> None:
            nonlocal utt
            while (item := await queue.get()) is not None:
                text, fut = item
                pcm = await fut
                if utt is None:
                    utt = uuid.uuid4().hex[:8]
                    await self._send_json(TtsStart(utterance_id=utt, sample_rate=self._tts.sample_rate))
                    log.debug("⏱ 1º áudio pronto: +%.0f ms", (time.perf_counter() - t0) * 1000)
                    if self.on_first_audio:
                        self.on_first_audio(utt)
                await self._send_bytes(pcm.tobytes())
                spoken.append(text)

        consumer = asyncio.create_task(consume())
        try:
            async for piece in limit_sentences(deltas, self.max_sentences):
                if not spoken and queue.empty():
                    log.debug("⏱ 1ª frase do texto: +%.0f ms %r", (time.perf_counter() - t0) * 1000, piece)
                queue.put_nowait((piece, loop.run_in_executor(self._executor, self._tts.synth, piece)))
            queue.put_nowait(None)
            await consumer
        finally:
            consumer.cancel()
            if utt is not None:
                await self._send_json(TtsEnd(utterance_id=utt))
        return utt, " ".join(spoken)

    async def say(self, text: str) -> str | None:
        async def one():
            yield text

        utt, _ = await self.speak_stream(one())
        return utt
