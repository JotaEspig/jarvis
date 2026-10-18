"""Segmentação de fala por VAD (lógica pura, sem áudio, testável).

Recebe um bloco de 32 ms por vez (probabilidade de fala + PCM) e emite eventos do protocolo
e os bytes de áudio que devem ir ao core.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from ..protocol import SpeechCancel, SpeechEnd, SpeechPause, SpeechStart

CHUNK_MS = 32


@dataclass
class SegmenterConfig:
    threshold: float = 0.5
    end_silence_ms: int = 600
    pause_ms: int = 250
    min_speech_ms: int = 250
    max_segment_s: float = 30.0
    start_ms: int = 64
    preroll_ms: int = 320
    # Barge-in: limiar e duração maiores, para eco residual não interromper a fala.
    barge_threshold: float = 0.75
    barge_in_ms: int = 250


class Segmenter:
    def __init__(self, cfg: SegmenterConfig):
        self.cfg = cfg
        self.open = False
        self._preroll: deque[bytes] = deque(maxlen=max(1, cfg.preroll_ms // CHUNK_MS))
        self._after_wake = False
        self._reset_segment()

    def _reset_segment(self) -> None:
        self.in_speech = False
        self._voiced_run = 0
        self._speech_ms = 0
        self._silence_ms = 0
        self._total_ms = 0
        self._paused = False
        self._barge = False

    def set_open(self, open_: bool) -> list:
        """Abre/fecha a janela. Fechar no meio de uma fala a descarta."""
        out: list = []
        if not open_ and self.in_speech:
            out.append(SpeechCancel())
            self._reset_segment()
        self.open = open_
        if not open_:
            self._after_wake = False
        return out

    def wake(self) -> list:
        """Wake-word detectada: abre a janela e marca a próxima fala como endereçada.

        Se havia um segmento em andamento, ele continha a própria wake-word e é descartado.
        """
        out = self.set_open(False) if self.in_speech else []
        self.open = True
        self._after_wake = True
        self._preroll.clear()
        return out

    def step(self, prob: float, pcm: bytes, *, playing: bool) -> tuple[list, bool]:
        """Processa um bloco. Retorna (saídas, barge_in_disparado)."""
        c = self.cfg
        out: list = []
        if not self.in_speech:
            if not self.open:
                self._preroll.append(pcm)
                return out, False
            thr, need = (c.barge_threshold, c.barge_in_ms) if playing else (c.threshold, c.start_ms)
            self._voiced_run = self._voiced_run + CHUNK_MS if prob >= thr else 0
            self._preroll.append(pcm)
            if self._voiced_run < need:
                return out, False
            self.in_speech = True
            self._barge = playing
            self._speech_ms = self._voiced_run
            out.append(SpeechStart(after_wake=self._after_wake, barge_in=playing))
            self._after_wake = False
            out.extend(self._preroll)
            self._total_ms = len(self._preroll) * CHUNK_MS
            self._preroll.clear()
            return out, playing

        out.append(pcm)
        self._total_ms += CHUNK_MS
        if prob >= c.threshold - 0.15:  # histerese
            self._speech_ms += CHUNK_MS
            self._silence_ms = 0
            self._paused = False
        else:
            self._silence_ms += CHUNK_MS
            if self._silence_ms >= c.pause_ms and not self._paused:
                self._paused = True
                out.append(SpeechPause())
        if self._silence_ms >= c.end_silence_ms or self._total_ms >= c.max_segment_s * 1000:
            if self._speech_ms >= c.min_speech_ms:
                out.append(SpeechEnd(duration_ms=self._total_ms))
            else:
                out.append(SpeechCancel())
            self._reset_segment()
        return out, False
