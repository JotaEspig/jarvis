"""Decide se uma fala ouvida na janela de conversa é para o assistente.

Barato e local: só os casos ambíguos vão para o Haiku, marcados como incertos, e ele pode
chamar a tool `ignore`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

Decision = Literal["accept", "ambiguous", "reject"]

_FILLERS = {"e", "hum", "hmm", "a", "ah", "eh", "uh", "tipo", "ne", "entao", "ahn", "ha", "aham", "mm"}  # já normalizados
_DIRECT_CUES = re.compile(
    r"^(cria|crie|faz|faça|faca|abre|abra|mostra|mostre|roda|rode|procura|procure|busca|busque|"
    r"pesquisa|pesquise|lista|liste|muda|mude|altera|altere|adiciona|adicione|remove|remova|"
    r"corrige|corrija|explica|explique|para|pare|cancela|cancele|continua|continue|manda|mande|"
    r"toca|toque|me|você|voce|pode|consegue|qual|quais|quanto|quantos|como|onde|quando|por que|"
    r"porque|o que|sim|não|nao|isso|pode ser|beleza|ok|okay|valeu|obrigado|obrigada|tchau|"
    r"encerra|encerrar|chega|só isso|so isso|usa|use|usar|roda|verifica|verifique|checa|cheque|"
    r"entao|agora|no projeto|na pasta|e tambem|faz tambem|status|instala|instale|atualiza|atualize|"
    r"testa|teste|revisa|revise|analisa|analise|compara|compare|escreve|escreva|gera|gere)\b"
)
_THIRD_PERSON = re.compile(r"\b(ele|ela|eles|elas|fulano|o cara|a mina)\b")


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKD", text.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^\w\s]", " ", t).strip()


def mentions_name(text: str, name: str) -> bool:
    n = normalize(name)
    t = normalize(text)
    variants = {n, n.replace("j", "dj"), "jarvas", "javis", "ja vis", "jarviz", "jervis"} if n == "jarvis" else {n}
    return any(re.search(rf"\b{re.escape(v)}\b", t) for v in variants)


@dataclass
class AddressContext:
    after_wake: bool = False
    barge_in: bool = False
    since_assistant_s: float = 1e9  # tempo desde o fim da última fala do assistente
    assistant_asked: bool = False  # a última fala do assistente terminou em pergunta
    pending_request: bool = False  # há pergunta/aprovação de tarefa esperando resposta
    after_ignored: bool = False  # houve fala ignorada há pouco (conversa paralela em curso)
    name: str = "Jarvis"


def decide(text: str, ctx: AddressContext) -> Decision:
    words = normalize(text).split()
    if not words:
        return "reject"
    if ctx.after_wake or ctx.barge_in or mentions_name(text, ctx.name):
        return "accept"
    if all(w in _FILLERS for w in words):
        return "reject"
    # Resposta a uma pergunta do assistente (ou de uma tarefa) logo em seguida: certa.
    if (ctx.assistant_asked or ctx.pending_request) and ctx.since_assistant_s < 10.0:
        return "accept"
    # Conversa paralela em curso: falas genéricas em seguida provavelmente são continuação dela.
    if ctx.after_ignored:
        return "ambiguous"
    # Fora isso, "ambíguo" custa o mesmo que "aceito" (uma chamada ao Haiku), só que com a chance
    # de ele ignorar. Por isso só aceitamos direto quando há sinais fortes.
    score = 0
    norm = " ".join(words)
    if _DIRECT_CUES.match(norm):
        score += 1
    if _THIRD_PERSON.search(norm):
        score -= 1
    if ctx.since_assistant_s < 15:
        score += 1
    if ctx.since_assistant_s < 4:
        score += 1
    if len(words) <= 2:
        score -= 1
    if score >= 2:
        return "accept"
    if score >= 0:
        return "ambiguous"
    return "reject"
