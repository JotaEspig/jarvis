"""Pré-baixa os modelos de voz (Whisper + Piper).

Usado no build do Docker e pelo install.sh para deixar a app pronta offline.
Rode: `python -m jarvis.download`
"""

from __future__ import annotations

from .config import settings


def main() -> None:
    print(f"Baixando voz Piper: {settings.piper_voice} …")
    from .voice.tts import ensure_voice

    ensure_voice()

    print(f"Baixando modelo Whisper: {settings.whisper_model} …")
    from .voice.stt import _model

    _model()  # instanciar dispara o download do modelo

    print("Modelos prontos.")


if __name__ == "__main__":
    main()
