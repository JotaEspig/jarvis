"""Camada de voz local: STT (faster-whisper) e TTS (Piper)."""

from .stt import transcribe
from .tts import synthesize, synthesize_sentences

__all__ = ["transcribe", "synthesize", "synthesize_sentences"]
