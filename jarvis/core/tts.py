"""TTS local: Kokoro (padrão, mais natural) ou Piper (mais rápido)."""

from __future__ import annotations

import logging
import time
from typing import Protocol

import numpy as np

from ..config import TtsConfig
from ..download import KOKORO_MODEL, KOKORO_VOICES, piper_voice_paths

log = logging.getLogger(__name__)


class Tts(Protocol):
    sample_rate: int

    def synth(self, text: str) -> np.ndarray:
        """Texto → PCM int16 mono em `sample_rate`."""


class KokoroTts:
    sample_rate = 24000

    def __init__(self, cfg: TtsConfig):
        from kokoro_onnx import Kokoro

        self._k = Kokoro(str(KOKORO_MODEL), str(KOKORO_VOICES))
        self._voice = cfg.kokoro_voice
        self._speed = cfg.speed

    def synth(self, text: str) -> np.ndarray:
        audio, sr = self._k.create(text, voice=self._voice, speed=self._speed, lang="pt-br")
        assert sr == self.sample_rate
        return (np.clip(audio, -1, 1) * 32767).astype(np.int16)


class PiperTts:
    def __init__(self, cfg: TtsConfig):
        from piper import PiperVoice

        model, _ = piper_voice_paths(cfg.piper_voice)
        self._v = PiperVoice.load(str(model))
        self.sample_rate = self._v.config.sample_rate
        self._length_scale = 1.0 / cfg.speed

    def synth(self, text: str) -> np.ndarray:
        from piper import SynthesisConfig

        chunks = list(self._v.synthesize(text, SynthesisConfig(length_scale=self._length_scale)))
        if not chunks:
            return np.zeros(0, dtype=np.int16)
        return np.concatenate([c.audio_int16_array for c in chunks])


def load_tts(cfg: TtsConfig) -> Tts:
    t = time.perf_counter()
    tts: Tts = KokoroTts(cfg) if cfg.engine == "kokoro" else PiperTts(cfg)
    # Aquece (a primeira inferência do onnxruntime é bem mais lenta).
    tts.synth("Ok.")
    log.info("TTS %s pronto em %.1fs", cfg.engine, time.perf_counter() - t)
    return tts
