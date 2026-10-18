"""CLI do Jarvis.

  jarvis run                 core + edge no mesmo processo (Linux nativo)
  jarvis core                só o core (ex.: no WSL, com o edge rodando no Windows)
  jarvis edge [--loopback]   só o edge de áudio
  jarvis doctor              checa credencial, latência do Haiku, modelos e áudio
  jarvis say "texto"         fala um texto localmente (testa o TTS)
  jarvis wakeword [--model M] mostra o score da wake-word ao vivo
  jarvis projects [add NOME CAMINHO]
  jarvis download [edge|core|all]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys


def _logging(verbose: bool) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname).1s %(name)s: %(message)s", datefmt="%H:%M:%S")
    for noisy in ("websockets", "httpx", "urllib3", "asyncio", "claude_agent_sdk"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def cmd_run(args) -> None:
    from .config import settings
    from .core.main import serve
    from .download import download_core, download_edge
    from .edge.main import Edge

    download_edge()
    download_core(settings.tts.piper_voice)

    async def both():
        core = asyncio.create_task(serve())
        await asyncio.sleep(0.5)
        edge = Edge(f"ws://{settings.link.host}:{settings.link.port}")
        await asyncio.wait([core, asyncio.create_task(edge.run())], return_when=asyncio.FIRST_COMPLETED)

    asyncio.run(both())


def cmd_core(args) -> None:
    from .config import settings
    from .core.main import serve
    from .download import download_core

    download_core(settings.tts.piper_voice)
    asyncio.run(serve())


def cmd_edge(args) -> None:
    from .edge.main import main as edge_main

    edge_main((["--loopback"] if args.loopback else []) + (["-v"] if args.verbose else []))


def cmd_say(args) -> None:
    import sounddevice as sd

    from .config import settings
    from .core.speech_out import sanitize
    from .core.tts import load_tts

    tts = load_tts(settings.tts)
    import time

    t = time.perf_counter()
    pcm = tts.synth(sanitize(" ".join(args.text)))
    print(f"síntese: {(time.perf_counter() - t) * 1000:.0f} ms para {len(pcm) / tts.sample_rate:.1f} s de áudio")
    sd.play(pcm, tts.sample_rate)
    sd.wait()


def cmd_projects(args) -> None:
    from pathlib import Path

    from .config import settings
    from .core.projects import ProjectRegistry

    reg = ProjectRegistry(settings.projects_root)
    if args.action == "add":
        reg.add(args.name, Path(args.path))
    for name, path in sorted(reg.aliases.items()):
        mark = "*" if reg.current == path else " "
        print(f"{mark} {name:24} {path}")


def cmd_wakeword(args) -> None:
    """Mostra o score da wake-word ao vivo (para testar um modelo e calibrar o limiar)."""
    import queue

    import numpy as np
    import sounddevice as sd

    from .config import settings
    from .download import download_edge
    from .edge.listener import OWW_CHUNK, load_wakeword

    download_edge()
    model = args.model or settings.wakeword.model
    oww = load_wakeword(model)
    q: queue.Queue = queue.Queue()
    print(f"modelo: {model} · limiar atual: {settings.wakeword.threshold} — fale a wake-word (Ctrl+C sai)")
    with sd.InputStream(samplerate=16000, channels=1, dtype="int16", blocksize=OWW_CHUNK,
                        callback=lambda d, *_: q.put(d[:, 0].copy())):
        while True:
            score = max(oww.predict(q.get()).values())
            if score >= 0.05:
                bar = "█" * int(score * 40)
                hit = "  ← DISPARA" if score >= settings.wakeword.threshold else ""
                print(f"{score:5.2f} {bar}{hit}")


def cmd_download(args) -> None:
    from .download import main as dl

    dl([args.what])


def cmd_doctor(args) -> None:
    from .doctor import run

    sys.exit(asyncio.run(run()))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="jarvis", description="Jarvis — assistente pessoal por voz")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="core + edge (Linux)").set_defaults(fn=cmd_run)
    sub.add_parser("core", help="só o core").set_defaults(fn=cmd_core)
    p = sub.add_parser("edge", help="só o edge de áudio")
    p.add_argument("--loopback", action="store_true")
    p.set_defaults(fn=cmd_edge)
    sub.add_parser("doctor", help="diagnóstico").set_defaults(fn=cmd_doctor)
    p = sub.add_parser("say", help="testa o TTS")
    p.add_argument("text", nargs="+")
    p.set_defaults(fn=cmd_say)
    p = sub.add_parser("projects", help="lista/adiciona projetos")
    p.add_argument("action", nargs="?", choices=["list", "add"], default="list")
    p.add_argument("name", nargs="?")
    p.add_argument("path", nargs="?")
    p.set_defaults(fn=cmd_projects)
    p = sub.add_parser("wakeword", help="testa a wake-word ao vivo")
    p.add_argument("--model", help="nome pré-treinado ou caminho .onnx/.tflite")
    p.set_defaults(fn=cmd_wakeword)
    p = sub.add_parser("download", help="baixa os modelos locais")
    p.add_argument("what", nargs="?", default="all", choices=["edge", "core", "all"])
    p.set_defaults(fn=cmd_download)
    args = ap.parse_args(argv)
    _logging(args.verbose)
    try:
        args.fn(args)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
