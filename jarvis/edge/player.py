"""Playback do TTS e dos earcons, com parada imediata (barge-in) e referência para o AEC."""

from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Callable

import numpy as np

from . import earcons
from .aec import EchoCanceller, resample

log = logging.getLogger(__name__)

OUT_RATE = 24000  # taxa do Kokoro; outras fontes são reamostradas para ela


class Player:
    def __init__(self, aec: EchoCanceller, device=None, on_done: Callable[[str, bool], None] | None = None):
        import sounddevice as sd

        self._aec = aec
        self._on_done = on_done
        self._lock = threading.Lock()
        self._queue: deque[np.ndarray] = deque()
        self._cur: np.ndarray | None = None
        self._pos = 0
        self._utt_id: str | None = None
        self._utt_rate = OUT_RATE
        self._utt_open = False
        self._stream = sd.OutputStream(
            samplerate=OUT_RATE, channels=1, dtype="float32", device=device,
            blocksize=480, latency="low", callback=self._callback,
        )
        self._stream.start()

    @property
    def output_latency_ms(self) -> float:
        return float(self._stream.latency) * 1000

    @property
    def playing(self) -> bool:
        with self._lock:
            return self._utt_open or self._cur is not None or bool(self._queue)

    @property
    def speaking(self) -> bool:
        """Há uma fala do TTS (não só earcon) em andamento."""
        with self._lock:
            return self._utt_id is not None

    # ---- fala do TTS ---------------------------------------------------------------------

    def begin(self, utterance_id: str, rate: int) -> None:
        with self._lock:
            self._utt_id, self._utt_rate, self._utt_open = utterance_id, rate, True

    def feed(self, pcm16: bytes) -> None:
        x = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
        with self._lock:
            if self._utt_id is None:  # chegou depois de um stop: descarta
                return
            self._queue.append(resample(x, self._utt_rate, OUT_RATE))

    def end(self, utterance_id: str) -> None:
        with self._lock:
            if self._utt_id == utterance_id:
                self._utt_open = False
        self._maybe_done()

    def stop(self) -> None:
        """Corta tudo na hora (barge-in / stop do core)."""
        with self._lock:
            utt = self._utt_id
            self._queue.clear()
            self._cur, self._pos = None, 0
            self._utt_id, self._utt_open = None, False
        if utt and self._on_done:
            self._on_done(utt, True)

    # ---- earcons -------------------------------------------------------------------------

    def earcon(self, name: str) -> None:
        with self._lock:
            self._queue.append(earcons.render(name, OUT_RATE))

    # ---- callback de áudio ---------------------------------------------------------------

    def _callback(self, out, frames, _time, _status) -> None:
        buf = np.zeros(frames, dtype=np.float32)
        filled = 0
        with self._lock:
            while filled < frames:
                if self._cur is None:
                    if not self._queue:
                        break
                    self._cur, self._pos = self._queue.popleft(), 0
                n = min(frames - filled, len(self._cur) - self._pos)
                buf[filled:filled + n] = self._cur[self._pos:self._pos + n]
                filled += n
                self._pos += n
                if self._pos >= len(self._cur):
                    self._cur = None
            drained = self._cur is None and not self._queue and not self._utt_open and self._utt_id is not None
        out[:, 0] = buf
        if filled:
            self._aec.push_reference(buf, OUT_RATE)
        if drained:
            threading.Thread(target=self._maybe_done, daemon=True).start()

    def _maybe_done(self) -> None:
        with self._lock:
            if self._utt_id is None or self._utt_open or self._cur is not None or self._queue:
                return
            utt, self._utt_id = self._utt_id, None
        if self._on_done:
            self._on_done(utt, False)

    def close(self) -> None:
        self._stream.stop()
        self._stream.close()
