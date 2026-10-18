"""Sons curtos de feedback (gerados, sem arquivos)."""

from __future__ import annotations

import numpy as np

# (frequências em Hz, duração de cada nota em s, volume)
_SPECS: dict[str, tuple[list[float], float, float]] = {
    "wake": ([880.0, 1320.0], 0.07, 0.25),
    "ack": ([1046.5], 0.05, 0.18),
    "done": ([784.0, 1046.5, 1318.5], 0.08, 0.25),
    "error": ([440.0, 330.0], 0.12, 0.25),
    "close": ([1320.0, 880.0], 0.06, 0.18),
}


def render(name: str, rate: int) -> np.ndarray:
    """Retorna o earcon como float32 mono na taxa `rate`."""
    freqs, dur, vol = _SPECS[name]
    n = int(rate * dur)
    t = np.arange(n) / rate
    env = np.minimum(1.0, np.minimum(t, dur - t) / 0.01)  # rampas de 10 ms (sem cliques)
    notes = [vol * env * np.sin(2 * np.pi * f * t) for f in freqs]
    return np.concatenate(notes).astype(np.float32)
