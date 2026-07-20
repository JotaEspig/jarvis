"""Configuração central do Jarvis (lida de env / .env via pydantic-settings)."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="JARVIS_",
        extra="ignore",
    )

    # --- Servidor web ---
    host: str = "127.0.0.1"
    port: int = 8765

    # --- Autenticação Anthropic ---
    # Usada tanto pelo SDK `anthropic` (Jarvis/Haiku) quanto pelo `claude-agent-sdk` (worker).
    # Lida do ambiente padrão ANTHROPIC_API_KEY se não vier via JARVIS_ANTHROPIC_API_KEY.
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")

    # --- Modelos ---
    jarvis_model: str = "claude-haiku-4-5"  # cérebro conversacional (barato)
    worker_model_simple: str = "claude-sonnet-5"  # tarefas simples
    worker_model_complex: str = "claude-opus-4-8"  # tarefas complexas
    worker_fallback_model: str = "claude-opus-4-8"

    # --- Repositório alvo do worker (opcional) ---
    # Diretório em que o coding agent atua. É OPCIONAL e definido dinamicamente na
    # sessão (pela UI). Só é obrigatório para ALTERAR arquivos; sem ele, o Jarvis
    # funciona em modo conversa (o worker pode raciocinar, mas não escreve).
    # Este valor serve apenas de padrão inicial da sessão (ex.: /workspace no Docker).
    target_repo: Path | None = None

    # --- Voz ---
    whisper_model: str = "small"  # tiny/base/small/medium/large-v3
    whisper_device: str = "auto"  # auto/cpu/cuda
    whisper_language: str = "pt"
    piper_voice: str = "pt_BR-faber-medium"  # voz Piper pt-BR
    voice_dir: Path = Field(
        default_factory=lambda: Path.home() / ".cache" / "jarvis" / "voices"
    )

    # --- Política de aprovação ---
    # Ferramentas que exigem aprovação por voz antes de rodar.
    approval_required_tools: tuple[str, ...] = ("Bash", "Edit", "Write", "NotebookEdit")


settings = Settings()  # instância única importável
