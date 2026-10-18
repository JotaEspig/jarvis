"""Silero VAD v5 via onnxruntime (sem torch). Opera em blocos de 512 amostras a 16 kHz."""

from __future__ import annotations

from pathlib import Path

import numpy as np

CHUNK = 512
_CONTEXT = 64


class SileroVad:
    def __init__(self, model_path: Path):
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        self._sess = ort.InferenceSession(str(model_path), sess_options=opts, providers=["CPUExecutionProvider"])
        self._sr = np.array(16000, dtype=np.int64)
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, _CONTEXT), dtype=np.float32)

    def __call__(self, chunk: np.ndarray) -> float:
        """Probabilidade de fala de um bloco float32 [-1, 1] com 512 amostras."""
        x = np.concatenate([self._context, chunk.reshape(1, CHUNK).astype(np.float32)], axis=1)
        out, self._state = self._sess.run(None, {"input": x, "state": self._state, "sr": self._sr})
        self._context = x[:, -_CONTEXT:]
        return float(out[0][0])
