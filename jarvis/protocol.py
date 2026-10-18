"""Protocolo edge ⇄ core (WebSocket local).

- Mensagens de texto: JSON com um campo `type` (modelos abaixo).
- Mensagens binárias edge→core: PCM16 mono 16 kHz do segmento de fala em andamento
  (só entre `speech_start` e `speech_end`).
- Mensagens binárias core→edge: PCM16 mono do TTS da fala em andamento, na taxa
  anunciada em `tts_start`.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, TypeAdapter

MIC_RATE = 16000


# ---- edge → core ---------------------------------------------------------------------------

class Hello(BaseModel):
    type: Literal["hello"] = "hello"
    platform: str
    version: str


class Wake(BaseModel):
    type: Literal["wake"] = "wake"
    score: float


class SpeechStart(BaseModel):
    type: Literal["speech_start"] = "speech_start"
    # True quando a fala começou logo após a wake-word (endereçada com certeza).
    after_wake: bool = False
    # True quando a fala interrompeu o playback (barge-in).
    barge_in: bool = False


class SpeechPause(BaseModel):
    """Pausa curta dentro da fala: o core pode transcrever o trecho já recebido."""

    type: Literal["speech_pause"] = "speech_pause"


class SpeechEnd(BaseModel):
    type: Literal["speech_end"] = "speech_end"
    duration_ms: int


class SpeechCancel(BaseModel):
    """Segmento descartado (curto demais / ruído)."""

    type: Literal["speech_cancel"] = "speech_cancel"


class PlaybackDone(BaseModel):
    type: Literal["playback_done"] = "playback_done"
    utterance_id: str
    interrupted: bool = False


class UserCommand(BaseModel):
    """Ação vinda da bandeja/UI do edge (ex.: encerrar conversa, cancelar tarefa)."""

    type: Literal["user_command"] = "user_command"
    command: str
    arg: str | None = None


EdgeMessage = Annotated[
    Union[Hello, Wake, SpeechStart, SpeechPause, SpeechEnd, SpeechCancel, PlaybackDone, UserCommand],
    Field(discriminator="type"),
]


# ---- core → edge ---------------------------------------------------------------------------

class TaskInfo(BaseModel):
    id: str
    title: str
    status: str


class State(BaseModel):
    type: Literal["state"] = "state"
    state: Literal["idle", "listening", "thinking", "speaking"]
    conversation_open: bool
    tasks: list[TaskInfo] = []


class Listen(BaseModel):
    """Abre/fecha a janela de conversa: aberta, o edge transmite fala sem wake-word."""

    type: Literal["listen"] = "listen"
    open: bool
    timeout_s: float = 0.0


class TtsStart(BaseModel):
    type: Literal["tts_start"] = "tts_start"
    utterance_id: str
    sample_rate: int


class TtsEnd(BaseModel):
    type: Literal["tts_end"] = "tts_end"
    utterance_id: str


class StopAudio(BaseModel):
    type: Literal["stop_audio"] = "stop_audio"


class Earcon(BaseModel):
    type: Literal["earcon"] = "earcon"
    name: Literal["wake", "ack", "done", "error", "close"]


class Notify(BaseModel):
    type: Literal["notify"] = "notify"
    title: str
    body: str


CoreMessage = Annotated[
    Union[State, Listen, TtsStart, TtsEnd, StopAudio, Earcon, Notify],
    Field(discriminator="type"),
]

_edge_adapter: TypeAdapter = TypeAdapter(EdgeMessage)
_core_adapter: TypeAdapter = TypeAdapter(CoreMessage)


def parse_edge(raw: str | bytes):
    return _edge_adapter.validate_json(raw)


def parse_core(raw: str | bytes):
    return _core_adapter.validate_json(raw)


def dump(msg: BaseModel) -> str:
    return msg.model_dump_json()
