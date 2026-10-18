"""STT local com Parakeet TDT v3 (sherpa-onnx).

Para baixar a latência, a fala é decodificada **por trechos**: a cada pausa curta enviada pelo edge
(`speech_pause`), o trecho acumulado já vai para a CPU enquanto o usuário continua falando. No fim
da fala, só falta o último trecho.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from concurrent.futures import Executor

import numpy as np

from ..config import SttConfig
from ..download import PARAKEET_DIR, fetch_parakeet
from ..protocol import MIC_RATE

log = logging.getLogger(__name__)

# O Parakeet às vezes "alucina" uma muleta em inglês no silêncio final ("Yeah.", "Okay.").
_TRAILING_FILLER = re.compile(r"(?:(?<=[.!?,])|^)\s*(yeah|yes|okay|ok|uh|um|mm+(-hmm)?|hmm+|oh|ah|right|so)[.!?]*\s*$",
                              re.I)


def clean_transcript(text: str) -> str:
    prev = None
    while prev != text:  # pode haver mais de uma ("Okay. Yeah.")
        prev, text = text, _TRAILING_FILLER.sub("", text).strip()
    return text


class Stt:
    def __init__(self, cfg: SttConfig):
        import sherpa_onnx

        fetch_parakeet()
        self.cfg = cfg
        threads = cfg.threads or max(1, (os.cpu_count() or 2) - 1)
        d = PARAKEET_DIR
        t = time.perf_counter()
        self._rec = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(d / "encoder.int8.onnx"), decoder=str(d / "decoder.int8.onnx"),
            joiner=str(d / "joiner.int8.onnx"), tokens=str(d / "tokens.txt"),
            num_threads=threads, model_type="nemo_transducer",
        )
        log.info("STT carregado em %.1fs (%d threads)", time.perf_counter() - t, threads)

    def transcribe(self, pcm16: bytes) -> str:
        if not pcm16:
            return ""
        audio = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
        s = self._rec.create_stream()
        s.accept_waveform(MIC_RATE, audio)
        self._rec.decode_stream(s)
        return s.result.text.strip()


class Utterance:
    """Uma fala do usuário sendo recebida e decodificada por trechos."""

    def __init__(self, stt: Stt, executor: Executor, trim_tail_ms: int = 0):
        self._stt = stt
        # Silêncio que o VAD esperou para fechar a fala: cortado antes de transcrever.
        self._trim = int(trim_tail_ms * MIC_RATE / 1000) * 2
        self._executor = executor
        self._buf = bytearray()
        self._parts: list[asyncio.Future] = []
        self.started = time.perf_counter()

    def add(self, pcm16: bytes) -> None:
        self._buf.extend(pcm16)

    def _flush(self) -> None:
        if self._buf:
            chunk, self._buf = bytes(self._buf), bytearray()
            loop = asyncio.get_running_loop()
            self._parts.append(loop.run_in_executor(self._executor, self._stt.transcribe, chunk))

    def pause(self) -> None:
        if len(self._buf) >= self._stt.cfg.min_chunk_s * MIC_RATE * 2:
            self._flush()

    async def finish(self) -> str:
        if self._trim:
            del self._buf[max(0, len(self._buf) - self._trim):]
        self._flush()
        parts = await asyncio.gather(*self._parts)
        return clean_transcript(" ".join(p for p in parts if p).strip())

    def cancel(self) -> None:
        for f in self._parts:
            f.cancel()
