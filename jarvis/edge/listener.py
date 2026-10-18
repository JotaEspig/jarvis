"""Captura do microfone → AEC → wake-word + VAD → eventos/áudio para o core.

Tudo roda numa thread própria; o resultado sai por `emit` (thread-safe do lado de quem recebe).
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np

from ..config import Settings
from ..protocol import MIC_RATE, Wake
from .aec import FRAME, EchoCanceller
from .player import Player
from .segmenter import Segmenter, SegmenterConfig
from .vad import CHUNK, SileroVad

log = logging.getLogger(__name__)

OWW_CHUNK = 1280  # 80 ms
WAKE_DEBOUNCE_S = 2.0


def load_wakeword(model: str):
    from openwakeword.model import Model

    p = Path(model).expanduser()
    if p.is_file():
        fw = "tflite" if p.suffix == ".tflite" else "onnx"
        return Model(wakeword_models=[str(p)], inference_framework=fw)
    return Model(wakeword_models=[model], inference_framework="onnx")


class Listener:
    def __init__(self, cfg: Settings, aec: EchoCanceller, player: Player, vad_model: Path,
                 emit: Callable[[object], None], on_wake: Callable[[], None] | None = None):
        self._cfg = cfg
        self._aec = aec
        self._player = player
        self._emit = emit
        self._on_wake = on_wake
        self._vad = SileroVad(vad_model)
        self._oww = load_wakeword(cfg.wakeword.model)
        v = cfg.vad
        self.seg = Segmenter(SegmenterConfig(
            threshold=v.threshold, end_silence_ms=v.end_silence_ms, min_speech_ms=v.min_speech_ms,
            max_segment_s=v.max_segment_s, barge_in_ms=v.barge_in_ms,
        ))
        self._frames: queue.Queue[np.ndarray] = queue.Queue(maxsize=500)
        self._cmds: queue.Queue[tuple] = queue.Queue()
        self._stop = threading.Event()
        self._last_wake = 0.0
        self._thread = threading.Thread(target=self._run, name="listener", daemon=True)

    # ---- controle (qualquer thread) ------------------------------------------------------

    def set_open(self, open_: bool) -> None:
        self._cmds.put(("open", open_))

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # ---- thread de processamento ---------------------------------------------------------

    def _capture_cb(self, indata, _frames, _time, status) -> None:
        if status:
            log.debug("input status: %s", status)
        try:
            self._frames.put_nowait(indata[:, 0].copy())
        except queue.Full:
            pass

    def _run(self) -> None:
        import sounddevice as sd

        vad_buf = np.zeros(0, dtype=np.int16)
        oww_buf = np.zeros(0, dtype=np.int16)
        stream = sd.InputStream(samplerate=MIC_RATE, channels=1, dtype="int16", blocksize=FRAME,
                                device=self._cfg.audio.input_device, latency="low", callback=self._capture_cb)
        with stream:
            self._aec.set_delay_ms(int(stream.latency * 1000 + self._player.output_latency_ms))
            log.info("microfone aberto (wake-word: %s)", self._cfg.wakeword.model)
            while not self._stop.is_set():
                self._drain_cmds()
                try:
                    frame = self._frames.get(timeout=0.1)
                except queue.Empty:
                    continue
                clean = self._aec.process_capture(frame)
                vad_buf = np.concatenate([vad_buf, clean])
                oww_buf = np.concatenate([oww_buf, clean])
                if len(oww_buf) >= OWW_CHUNK:
                    self._check_wake(oww_buf[:OWW_CHUNK])
                    oww_buf = oww_buf[OWW_CHUNK:]
                while len(vad_buf) >= CHUNK:
                    chunk, vad_buf = vad_buf[:CHUNK], vad_buf[CHUNK:]
                    prob = self._vad(chunk.astype(np.float32) / 32768.0)
                    out, barge = self.seg.step(prob, chunk.tobytes(), playing=self._player.speaking)
                    if barge:
                        self._player.stop()
                    for item in out:
                        self._emit(item)

    def _drain_cmds(self) -> None:
        while True:
            try:
                cmd, arg = self._cmds.get_nowait()
            except queue.Empty:
                return
            if cmd == "open":
                for item in self.seg.set_open(arg):
                    self._emit(item)

    def _check_wake(self, chunk: np.ndarray) -> None:
        scores = self._oww.predict(chunk)
        score = max(scores.values()) if scores else 0.0
        now = time.monotonic()
        if score < self._cfg.wakeword.threshold or now - self._last_wake < WAKE_DEBOUNCE_S:
            return
        self._last_wake = now
        self._oww.reset()
        log.info("wake-word (%.2f)", score)
        if self._player.speaking:
            self._player.stop()
        for item in self.seg.wake():
            self._emit(item)
        self._vad.reset()
        self._emit(Wake(score=float(score)))
        if self._on_wake:
            self._on_wake()
