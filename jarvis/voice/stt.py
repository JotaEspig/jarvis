"""Speech-to-text local com faster-whisper.

Import da lib pesada é preguiçoso (dentro das funções) para manter o resto do
pacote leve e importável sem o modelo instalado.
"""

from __future__ import annotations

import io
from functools import lru_cache

from ..config import settings


@lru_cache(maxsize=1)
def _model():
    from faster_whisper import WhisperModel

    device = settings.whisper_device
    # compute_type sensato por device (int8 no CPU, float16 na GPU).
    if device == "cuda":
        compute_type = "float16"
    elif device == "cpu":
        compute_type = "int8"
    else:  # auto
        compute_type = "default"
    return WhisperModel(
        settings.whisper_model, device=device, compute_type=compute_type
    )


def transcribe(audio: bytes, language: str | None = None) -> str:
    """Transcreve áudio bruto (ex.: webm/opus do navegador, wav) em texto.

    faster-whisper decodifica via PyAV, então formatos comuns funcionam.
    """
    if not audio:
        return ""
    lang = language or settings.whisper_language
    segments, _info = _model().transcribe(
        io.BytesIO(audio),
        language=lang,
        beam_size=5,
        vad_filter=True,
    )
    return "".join(seg.text for seg in segments).strip()
