"""Text-to-speech local com Piper (voz pt-BR).

Faz download do modelo de voz sob demanda (Hugging Face rhasspy/piper-voices) e
sintetiza WAV. Import da lib pesada é preguiçoso. A síntese por frase permite
começar a tocar o áudio antes de a resposta inteira terminar (menor latência).
"""

from __future__ import annotations

import io
import re
import wave
from functools import lru_cache
from pathlib import Path
from typing import Iterator
from urllib.request import urlretrieve

from ..config import settings

_HF_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"


def _voice_paths() -> tuple[Path, Path]:
    """Caminhos locais (.onnx, .onnx.json) do modelo de voz configurado."""
    voice = settings.piper_voice  # ex.: pt_BR-faber-medium
    d = settings.voice_dir
    return d / f"{voice}.onnx", d / f"{voice}.onnx.json"


def _download_url(suffix: str) -> str:
    voice = settings.piper_voice  # pt_BR-faber-medium
    locale, name, quality = voice.split("-")  # pt_BR, faber, medium
    family = locale.split("_")[0]  # pt
    return f"{_HF_BASE}/{family}/{locale}/{name}/{quality}/{voice}{suffix}"


def ensure_voice() -> Path:
    """Garante o modelo de voz baixado; retorna o caminho do .onnx."""
    onnx, cfg = _voice_paths()
    onnx.parent.mkdir(parents=True, exist_ok=True)
    if not onnx.exists():
        urlretrieve(_download_url(".onnx"), onnx)
    if not cfg.exists():
        urlretrieve(_download_url(".onnx.json"), cfg)
    return onnx


@lru_cache(maxsize=1)
def _voice():
    from piper import PiperVoice

    onnx = ensure_voice()
    return PiperVoice.load(str(onnx))


def _sample_rate(voice) -> int:
    cfg = getattr(voice, "config", None)
    return int(getattr(cfg, "sample_rate", 22050)) if cfg else 22050


def synthesize(text: str) -> bytes:
    """Sintetiza `text` inteiro e retorna bytes de um WAV (16-bit mono)."""
    text = text.strip()
    if not text:
        return b""
    voice = _voice()
    buf = io.BytesIO()

    # API nova: synthesize_wav(text, wav_file)
    if hasattr(voice, "synthesize_wav"):
        with wave.open(buf, "wb") as wav:
            voice.synthesize_wav(text, wav)
        return buf.getvalue()

    # API por chunks: synthesize(text) -> iterável de AudioChunk
    try:
        chunks = list(voice.synthesize(text))
        if chunks and hasattr(chunks[0], "audio_int16_bytes"):
            rate = getattr(chunks[0], "sample_rate", _sample_rate(voice))
            with wave.open(buf, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(rate)
                for ch in chunks:
                    wav.writeframes(ch.audio_int16_bytes)
            return buf.getvalue()
    except TypeError:
        pass

    # API antiga: synthesize(text, wav_file)
    with wave.open(buf, "wb") as wav:
        voice.synthesize(text, wav)
    return buf.getvalue()


_SENT_RE = re.compile(r"[^.!?…\n]+[.!?…]?", re.UNICODE)


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_RE.findall(text) if s.strip()]


def synthesize_sentences(text: str) -> Iterator[bytes]:
    """Sintetiza frase a frase (um WAV por frase) para streaming de baixa latência."""
    for sentence in split_sentences(text):
        audio = synthesize(sentence)
        if audio:
            yield audio
