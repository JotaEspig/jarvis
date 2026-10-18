"""Processo do core: carrega STT/TTS, conecta o front e serve o edge por WebSocket."""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from ..config import settings
from ..protocol import parse_edge

log = logging.getLogger("jarvis.core")


async def serve(stop: asyncio.Event | None = None) -> None:
    import websockets

    from .auth import harden_environment
    from .session import Session
    from .stt import Stt
    from .tts import load_tts

    info = harden_environment()
    log.info("credencial: %s", info.source)
    executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="voice")
    loop = asyncio.get_running_loop()
    stt_f = loop.run_in_executor(executor, Stt, settings.stt)
    tts_f = loop.run_in_executor(executor, load_tts, settings.tts)
    stt, tts = await asyncio.gather(stt_f, tts_f)
    session = Session(settings, stt, tts, executor)
    await session.front.start()

    async def handler(ws) -> None:
        if session.link is not None:
            log.info("novo edge conectado; desconectando o anterior")
            await session.link.close()
        await session.attach(ws)
        try:
            async for raw in ws:
                if isinstance(raw, bytes):
                    session.handle_audio(raw)
                    continue
                try:
                    msg = parse_edge(raw)
                except Exception:
                    log.warning("mensagem inválida do edge: %.200s", raw)
                    continue
                await session.handle(msg)
        except websockets.ConnectionClosed:
            pass
        finally:
            await session.detach(ws)
            log.info("edge desconectado")

    async with websockets.serve(handler, settings.link.host, settings.link.port, max_size=None, ping_interval=20):
        log.info("core pronto em ws://%s:%d — diga a wake-word", settings.link.host, settings.link.port)
        await (stop.wait() if stop else asyncio.Future())
    await session.front.stop()
