"""Cérebro do Jarvis (Haiku 4.5) via SDK `anthropic`.

Responsabilidades:
  1. Intake conversacional por voz (multi-turn, sondando "Isso é tudo?").
  2. Handoff: gera um prompt bem formulado e classifica a complexidade da tarefa,
     que é mapeada para (modelo, effort) do worker.
  3. Ponte/tradutor: verbaliza perguntas do worker, interpreta a resposta falada do
     usuário, e resume a saída do worker para voz.

O cliente é criado de forma preguiçosa para o módulo importar sem a chave/SDK.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from functools import lru_cache
from typing import AsyncIterator, Literal

from .config import settings

_TEXT_MEDIA = ("text/", "application/json", "application/xml", "application/x-yaml")


def _attachment_block(att: dict) -> dict:
    """Converte um anexo {kind, media_type, data(base64), name} num bloco de conteúdo."""
    mt = att.get("media_type") or ""
    data = att.get("data") or ""
    name = att.get("name") or "arquivo"
    if att.get("kind") == "image" or mt.startswith("image/"):
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": mt or "image/png", "data": data},
        }
    if mt == "application/pdf":
        return {
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": data},
        }
    # Texto/código: embute o conteúdo como texto.
    is_text = mt.startswith(_TEXT_MEDIA) or not mt
    if is_text:
        try:
            text = base64.b64decode(data).decode("utf-8")
            return {"type": "text", "text": f"Arquivo anexado ({name}):\n\n{text}"}
        except (ValueError, UnicodeDecodeError):
            pass
    return {
        "type": "text",
        "text": f"[arquivo anexado: {name} ({mt or 'desconhecido'}) — conteúdo não legível como texto]",
    }

Complexity = Literal["trivial", "simple", "moderate", "complex", "architectural"]

# Complexidade -> (modelo do worker, effort). Effort e modelo escalam juntos.
_COMPLEXITY_MAP: dict[str, tuple[str, str]] = {
    "trivial": (settings.worker_model_simple, "low"),
    "simple": (settings.worker_model_simple, "medium"),
    "moderate": (settings.worker_model_simple, "high"),
    "complex": (settings.worker_model_complex, "high"),
    "architectural": (settings.worker_model_complex, "xhigh"),
}


@dataclass
class Handoff:
    title: str
    prompt: str
    complexity: str
    rationale: str

    @property
    def model(self) -> str:
        return _COMPLEXITY_MAP[self.complexity][0]

    @property
    def effort(self) -> str:
        return _COMPLEXITY_MAP[self.complexity][1]


@dataclass
class InterpretedAnswer:
    answer: str
    needs_followup: bool
    followup_question: str | None


def _content_text(content) -> str:
    """Renderiza o conteúdo de uma mensagem (str ou lista de blocos) como texto."""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        btype = block.get("type")
        if btype == "text":
            parts.append(block["text"])
        elif btype == "image":
            parts.append("[imagem]")
        elif btype == "document":
            parts.append("[documento]")
    return " ".join(parts)


_INTAKE_SYSTEM = """\
Você é o Jarvis, um assistente de desenvolvimento que conversa por VOZ em português.
Você não escreve código nem executa nada — um agente mais forte (o "worker") fará o
trabalho depois, a partir de um prompt que você vai montar.

Seu papel agora é entender bem o que o usuário quer:
- Faça perguntas indagando até a tarefa ficar concreta (escopo, arquivos/áreas, critérios de pronto, restrições).
- Uma pergunta por vez, curta. Suas respostas serão FALADAS em voz alta: use frases curtas, sem markdown, sem listas longas, sem código.
- Quando achar que já entendeu tudo, NUNCA encerre calado: SEMPRE sonde com algo como "Isso é tudo?" ou "Quer acrescentar mais alguma coisa antes de eu acionar o agente?".
- Só considere encerrado quando o usuário disser explicitamente que é tudo.
"""

_HANDOFF_SYSTEM = """\
Dado o histórico da conversa entre o usuário e o Jarvis, produza um PROMPT de tarefa
bem formulado, autossuficiente e em português, para um coding agent (Claude) que vai
atuar no repositório do usuário. Inclua objetivo, contexto relevante mencionado, e
critérios de conclusão. Não invente requisitos que não foram ditos.

Também classifique a complexidade da tarefa em uma destas categorias:
- "trivial": mudança mínima, um arquivo, sem ambiguidade.
- "simple": pequena, escopo claro.
- "moderate": várias etapas ou arquivos, mas direto.
- "complex": exige planejamento, várias áreas, decisões de design.
- "architectural": mudança ampla/estrutural, alto risco, longo horizonte.

Responda SOMENTE com o JSON do schema fornecido.
"""


@lru_cache(maxsize=1)
def _client():
    from anthropic import AsyncAnthropic

    if settings.anthropic_api_key:
        return AsyncAnthropic(api_key=settings.anthropic_api_key)
    return AsyncAnthropic()  # resolve ANTHROPIC_API_KEY / perfil do ambiente


async def _json_call(system: str, user: str, schema: dict) -> dict:
    """Uma chamada com structured output; retorna o dict validado pelo schema."""
    resp = await _client().messages.create(
        model=settings.jarvis_model,
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"format": {"type": "json_schema", "schema": schema}},
    )
    text = next(b.text for b in resp.content if b.type == "text")
    return json.loads(text)


class JarvisBrain:
    """Mantém o histórico do intake e expõe as operações do Jarvis."""

    def __init__(self) -> None:
        self.history: list[dict] = []

    # ---- Intake --------------------------------------------------------
    async def reply_stream(
        self, user_text: str, attachments: list[dict] | None = None
    ) -> AsyncIterator[str]:
        """Turno de intake: transmite a resposta do Jarvis em pedaços de texto.

        `attachments` é uma lista opcional de {kind, media_type, data(base64), name}
        (imagens, PDFs, texto/código). Haiku é multimodal.
        """
        content: list[dict] = [_attachment_block(a) for a in (attachments or [])]
        if user_text:
            content.append({"type": "text", "text": user_text})
        if not content:
            return
        self.history.append({"role": "user", "content": content})
        parts: list[str] = []
        async with _client().messages.stream(
            model=settings.jarvis_model,
            max_tokens=600,
            system=_INTAKE_SYSTEM,
            messages=self.history,
        ) as stream:
            async for text in stream.text_stream:
                parts.append(text)
                yield text
        self.history.append({"role": "assistant", "content": "".join(parts)})

    async def reply(self, user_text: str, attachments: list[dict] | None = None) -> str:
        return "".join(
            [chunk async for chunk in self.reply_stream(user_text, attachments)]
        )

    # ---- Handoff -------------------------------------------------------
    async def generate_handoff(self) -> Handoff:
        transcript = "\n".join(
            f"{m['role']}: {_content_text(m['content'])}" for m in self.history
        )
        schema = {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "prompt": {"type": "string"},
                "complexity": {
                    "type": "string",
                    "enum": list(_COMPLEXITY_MAP.keys()),
                },
                "rationale": {"type": "string"},
            },
            "required": ["title", "prompt", "complexity", "rationale"],
            "additionalProperties": False,
        }
        data = await _json_call(
            _HANDOFF_SYSTEM, f"Histórico:\n{transcript}", schema
        )
        return Handoff(**data)

    # ---- Ponte / tradutor ---------------------------------------------
    async def phrase_question(self, worker_question: str) -> str:
        """Reformula a pergunta do worker em fala natural curta."""
        resp = await _client().messages.create(
            model=settings.jarvis_model,
            max_tokens=300,
            system=(
                "O coding agent que está trabalhando fez uma pergunta ao usuário. "
                "Reformule em português falado, curto e natural, para ser lido em voz "
                "alta. Devolva SÓ a pergunta, sem preâmbulo."
            ),
            messages=[{"role": "user", "content": worker_question}],
        )
        return next(b.text for b in resp.content if b.type == "text").strip()

    async def interpret_answer(
        self, worker_question: str, user_answer: str
    ) -> InterpretedAnswer:
        """Transforma a fala solta do usuário numa resposta limpa para o worker."""
        schema = {
            "type": "object",
            "properties": {
                "answer": {"type": "string"},
                "needs_followup": {"type": "boolean"},
                "followup_question": {"type": ["string", "null"]},
            },
            "required": ["answer", "needs_followup", "followup_question"],
            "additionalProperties": False,
        }
        user = (
            f"Pergunta do agente: {worker_question}\n"
            f"Resposta falada do usuário (transcrição): {user_answer}"
        )
        system = (
            "Extraia da fala do usuário uma resposta clara e objetiva para devolver ao "
            "coding agent. Se a fala não responder à pergunta, marque needs_followup=true "
            "e proponha uma followup_question curta; senão needs_followup=false e "
            "followup_question=null."
        )
        data = await _json_call(system, user, schema)
        return InterpretedAnswer(**data)

    async def summarize_for_voice(self, worker_text: str) -> str:
        """Resumo curto (para voz) da saída do worker."""
        resp = await _client().messages.create(
            model=settings.jarvis_model,
            max_tokens=300,
            system=(
                "Resuma para ser lido em voz alta, em 1 a 3 frases curtas, o que o "
                "agente fez ou respondeu. Sem markdown, sem código."
            ),
            messages=[{"role": "user", "content": worker_text}],
        )
        return next(b.text for b in resp.content if b.type == "text").strip()
