"""Configuração do Jarvis.

Fontes, em ordem de prioridade: variáveis de ambiente (`JARVIS_*`) > `~/.config/jarvis/config.toml`
> defaults abaixo. Chaves aninhadas no ambiente usam `__` (ex.: `JARVIS_STT__MODEL=small`).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict, TomlConfigSettingsSource

CONFIG_DIR = Path(os.environ.get("JARVIS_CONFIG_DIR", Path.home() / ".config" / "jarvis"))
DATA_DIR = Path(os.environ.get("JARVIS_DATA_DIR", Path.home() / ".local" / "share" / "jarvis"))
MODELS_DIR = DATA_DIR / "models"


class LinkConfig(BaseModel):
    """Conexão edge ⇄ core (WebSocket local)."""

    host: str = "127.0.0.1"
    port: int = 8766


class AudioConfig(BaseModel):
    input_device: str | int | None = None
    output_device: str | int | None = None
    aec: bool = True
    noise_suppression: bool = True


class WakeWordConfig(BaseModel):
    # Nome de um modelo pré-treinado do openWakeWord ou caminho para um .onnx/.tflite treinado.
    model: str = "hey_jarvis"
    threshold: float = 0.5
    notify: bool = True  # notificação no desktop a cada ativação


class VadConfig(BaseModel):
    threshold: float = 0.5
    # Silêncio que fecha um segmento de fala (fim do turno do usuário).
    end_silence_ms: int = 600
    min_speech_ms: int = 250
    max_segment_s: float = 30.0
    # Fala contínua durante o playback necessária para disparar o barge-in.
    barge_in_ms: int = 250


class SttConfig(BaseModel):
    # Parakeet TDT v3 (sherpa-onnx): multilíngue com pt, sem a janela fixa de 30 s do Whisper.
    threads: int = 0  # 0 = automático (núcleos - 1)
    # Trechos menores que isso não são decodificados numa pausa (evita cortar palavras).
    min_chunk_s: float = 1.5


class TtsConfig(BaseModel):
    engine: Literal["kokoro", "piper"] = "kokoro"
    kokoro_voice: str = "pm_alex"
    speed: float = 1.1
    piper_voice: str = "pt_BR-faber-medium"


class FrontConfig(BaseModel):
    model: str = "claude-haiku-4-5"
    max_sentences: int = 3
    # Janela de conversa: silêncio (após a última fala do assistente) que a encerra.
    window_s: float = 10.0
    # Janela curta reaberta quando uma tarefa termina e o assistente avisa.
    reopen_s: float = 6.0
    # Recicla a sessão do Haiku após N turnos (latência e cota).
    recycle_turns: int = 30


class BackConfig(BaseModel):
    model_default: str = "claude-sonnet-5-5"
    model_complex: str = "claude-opus-5-5"
    fallback_model: str = "claude-sonnet-5-5"
    max_parallel: int = 3


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="JARVIS_",
        env_nested_delimiter="__",
        toml_file=CONFIG_DIR / "config.toml",
        extra="ignore",
    )

    assistant_name: str = "Jarvis"
    user_title: str = "senhor"
    projects_root: list[Path] = [Path.home() / "dev"]

    link: LinkConfig = LinkConfig()
    audio: AudioConfig = AudioConfig()
    wakeword: WakeWordConfig = WakeWordConfig()
    vad: VadConfig = VadConfig()
    stt: SttConfig = SttConfig()
    tts: TtsConfig = TtsConfig()
    front: FrontConfig = FrontConfig()
    back: BackConfig = BackConfig()

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings, env_settings, TomlConfigSettingsSource(settings_cls))


settings = Settings()
