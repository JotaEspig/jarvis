"""Round-trip de voz local: TTS -> STT.

Requer `piper-tts` e `faster-whisper` instalados e faz download dos modelos na
primeira execução — por isso é pulado automaticamente se as libs não existirem.
As checagens de lógica leve (sem modelos) rodam sempre.
"""

import pytest

from jarvis.voice import tts


def test_split_sentences():
    assert tts.split_sentences("Olá! Tudo bem?") == ["Olá!", "Tudo bem?"]
    assert tts.split_sentences("") == []


def test_download_url():
    url = tts._download_url(".onnx")
    assert url.endswith("/pt/pt_BR/faber/medium/pt_BR-faber-medium.onnx")


@pytest.mark.slow
def test_tts_stt_roundtrip():
    pytest.importorskip("piper")
    pytest.importorskip("faster_whisper")
    from jarvis.voice import transcribe

    wav = tts.synthesize("teste de voz")
    assert wav[:4] == b"RIFF" and len(wav) > 1000  # WAV plausível

    text = transcribe(wav, language="pt")
    assert isinstance(text, str) and text.strip()  # transcreveu algo
