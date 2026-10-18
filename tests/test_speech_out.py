import asyncio

import numpy as np

from jarvis.core.speech_out import SentenceSplitter, SpeechOut, limit_sentences, sanitize
from jarvis.protocol import TtsEnd, TtsStart


async def _gen(parts):
    for p in parts:
        yield p


def _collect(parts, n=3):
    async def run():
        return [x async for x in limit_sentences(_gen(parts), n)]
    return asyncio.run(run())


def test_splitter_keeps_filenames_and_decimals():
    sp = SentenceSplitter()
    out = sp.feed("Criei o hello") + sp.feed(".py com versão 3.5 agora. Pronto") + sp.flush()
    assert out == ["Criei o hello.py com versão 3.5 agora.", "Pronto"]


def test_first_long_sentence_is_cut_at_comma():
    sp = SentenceSplitter()
    out = sp.feed("Vou criar o arquivo no projeto jarvis agora mesmo e depois, rodar os testes")
    assert out == ["Vou criar o arquivo no projeto jarvis agora mesmo e depois,"]


def test_limit_three_sentences_and_strip_markdown():
    out = _collect(["**Claro!** 😊 Um. Dois", ". Três. Quatro. Cinco."])
    assert out == ["Claro!", "Um.", "Dois."]


def test_sanitize_urls_and_code():
    assert "o link" in sanitize("veja https://x.com/a")
    assert "print" not in sanitize("```py\nprint(1)\n``` feito")


def test_speech_out_streams_one_utterance():
    sent = []

    class FakeTts:
        sample_rate = 24000

        def synth(self, text):
            return np.zeros(10, dtype=np.int16)

    async def send_json(m):
        sent.append(m)

    async def send_bytes(b):
        sent.append(b)

    async def run():
        from concurrent.futures import ThreadPoolExecutor

        so = SpeechOut(FakeTts(), ThreadPoolExecutor(1), send_json, send_bytes, 3)
        return await so.speak_stream(_gen(["Oi. ", "Tudo certo. ", "Feito."]))

    utt, spoken = asyncio.run(run())
    assert utt and spoken == "Oi. Tudo certo. Feito."
    assert isinstance(sent[0], TtsStart) and isinstance(sent[-1], TtsEnd)
    assert sum(isinstance(x, bytes) for x in sent) == 3


def test_clean_transcript_strips_hallucinated_fillers():
    from jarvis.core.stt import clean_transcript

    assert clean_transcript("Procure o projeto na pasta dev. Yeah.") == "Procure o projeto na pasta dev."
    assert clean_transcript("É esse projeto mesmo, tá? Okay. Yeah.") == "É esse projeto mesmo, tá?"
    assert clean_transcript("Okay.") == ""
    assert clean_transcript("deixa okay o layout") == "deixa okay o layout"
    assert clean_transcript("roda o build ok") == "roda o build ok"


def test_bracketed_meta_comments_are_never_spoken():
    assert _collect(["[Sem resposta — endereçamento ", "incerto, fala não dirigida a mim.]"]) == []
    assert _collect(["Ok, senhor. [nota: tarefa", " #2] Já despachei."]) == ["Ok, senhor.", "Já despachei."]
