"""Cancelamento de eco + supressão de ruído (WebRTC APM via livekit), igual no Linux e no Windows.

O microfone passa por `process_capture` em blocos de 10 ms. O que o player toca entra como
referência em `push_reference`, para que a voz do próprio assistente seja subtraída do mic.
Isso viabiliza o barge-in mesmo com caixa de som.
"""

from __future__ import annotations

import logging
import threading

import numpy as np

from ..protocol import MIC_RATE

log = logging.getLogger(__name__)

FRAME = MIC_RATE // 100  # 10 ms


def resample(x: np.ndarray, src: int, dst: int) -> np.ndarray:
    """Reamostragem linear (suficiente para a referência do AEC)."""
    if src == dst or len(x) == 0:
        return x
    n = int(round(len(x) * dst / src))
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def _to_i16(x: np.ndarray) -> np.ndarray:
    return (np.clip(x, -1.0, 1.0) * 32767).astype(np.int16)


class EchoCanceller:
    def __init__(self, *, aec: bool = True, noise_suppression: bool = True):
        self.enabled = aec or noise_suppression
        self._lock = threading.Lock()
        self._ref_buf = np.zeros(0, dtype=np.int16)
        self._apm = None
        if not self.enabled:
            return
        try:
            from livekit import rtc

            self._rtc = rtc
            self._apm = rtc.AudioProcessingModule(
                echo_cancellation=aec,
                noise_suppression=noise_suppression,
                high_pass_filter=True,
                auto_gain_control=True,
            )
        except Exception:  # sem livekit: segue sem AEC (barge-in só confiável com fone)
            log.exception("AEC indisponível; seguindo sem cancelamento de eco")
            self.enabled = False

    def set_delay_ms(self, ms: int) -> None:
        if self._apm is not None:
            with self._lock:
                self._apm.set_stream_delay_ms(int(ms))

    def _frame(self, pcm: np.ndarray):
        return self._rtc.AudioFrame(pcm.tobytes(), MIC_RATE, 1, FRAME)

    def process_capture(self, frame_i16: np.ndarray) -> np.ndarray:
        """Processa 10 ms (160 amostras int16) do microfone; retorna int16 processado."""
        if self._apm is None:
            return frame_i16
        f = self._frame(frame_i16)
        with self._lock:
            self._apm.process_stream(f)
        return np.frombuffer(f.data, dtype=np.int16).copy()

    def push_reference(self, played: np.ndarray, rate: int) -> None:
        """Alimenta o que acabou de ser tocado (float32 mono em `rate`) como referência."""
        if self._apm is None:
            return
        pcm = _to_i16(resample(played, rate, MIC_RATE))
        with self._lock:
            buf = np.concatenate([self._ref_buf, pcm])
            n = len(buf) // FRAME
            for i in range(n):
                self._apm.process_reverse_stream(self._frame(buf[i * FRAME:(i + 1) * FRAME]))
            self._ref_buf = buf[n * FRAME:]
