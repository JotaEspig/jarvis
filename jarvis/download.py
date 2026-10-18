"""Download dos modelos locais (voz, VAD, wake-word). `python -m jarvis.download [edge|core|all]`."""

from __future__ import annotations

import logging
import sys
import urllib.request
from pathlib import Path

from .config import MODELS_DIR

log = logging.getLogger(__name__)

KOKORO_BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
KOKORO_MODEL = MODELS_DIR / "kokoro-v1.0.int8.onnx"
KOKORO_VOICES = MODELS_DIR / "voices-v1.0.bin"
SILERO_VAD = MODELS_DIR / "silero_vad.onnx"
SILERO_URL = "https://github.com/snakers4/silero-vad/raw/v5.1.2/src/silero_vad/data/silero_vad.onnx"
PIPER_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/pt/pt_BR"
PARAKEET_NAME = "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"
PARAKEET_DIR = MODELS_DIR / PARAKEET_NAME
PARAKEET_URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{PARAKEET_NAME}.tar.bz2"


def fetch(url: str, dest: Path) -> Path:
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    log.info("baixando %s", url)
    print(f"↓ {dest.name}", file=sys.stderr)
    urllib.request.urlretrieve(url, tmp)
    tmp.rename(dest)
    return dest


def fetch_parakeet() -> Path:
    if (PARAKEET_DIR / "tokens.txt").is_file():
        return PARAKEET_DIR
    import tarfile

    archive = fetch(PARAKEET_URL, MODELS_DIR / f"{PARAKEET_NAME}.tar.bz2")
    with tarfile.open(archive) as tar:
        tar.extractall(MODELS_DIR, filter="data")
    archive.unlink()
    return PARAKEET_DIR


def piper_voice_paths(voice: str) -> tuple[Path, Path]:
    model = MODELS_DIR / f"{voice}.onnx"
    return model, model.with_suffix(".onnx.json")


def download_edge() -> None:
    fetch(SILERO_URL, SILERO_VAD)
    from openwakeword.utils import download_models

    download_models()


def download_core(piper_voice: str = "pt_BR-faber-medium") -> None:
    fetch_parakeet()
    fetch(f"{KOKORO_BASE}/kokoro-v1.0.int8.onnx", KOKORO_MODEL)
    fetch(f"{KOKORO_BASE}/voices-v1.0.bin", KOKORO_VOICES)
    name, quality = piper_voice.removeprefix("pt_BR-").rsplit("-", 1)
    model, cfg = piper_voice_paths(piper_voice)
    fetch(f"{PIPER_BASE}/{name}/{quality}/{piper_voice}.onnx", model)
    fetch(f"{PIPER_BASE}/{name}/{quality}/{piper_voice}.onnx.json", cfg)


def main(argv: list[str] | None = None) -> None:
    what = (argv or sys.argv[1:] or ["all"])[0]
    if what in ("edge", "all"):
        download_edge()
    if what in ("core", "all"):
        from .config import settings

        download_core(settings.tts.piper_voice)


if __name__ == "__main__":
    main()
