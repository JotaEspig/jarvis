"""Processo do edge: áudio local ⇄ core por WebSocket.

Uso: `jarvis-edge [--core ws://127.0.0.1:8766] [--loopback]`
No Windows (core no WSL), o `localhost` do Windows alcança o WSL2 automaticamente.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import platform

from .. import __version__
from ..config import settings
from ..download import SILERO_VAD, download_edge
from ..protocol import (Earcon, Hello, Listen, Notify, PlaybackDone, SpeechEnd, State, StopAudio, TtsEnd,
                        TtsStart, UserCommand, dump, parse_core)
from .aec import EchoCanceller
from .listener import Listener
from .player import Player
from .tray import Tray

log = logging.getLogger("jarvis.edge")


class Edge:
    def __init__(self, core_url: str, loopback: bool = False):
        self.core_url = core_url
        self.loopback = loopback
        self.loop = asyncio.get_running_loop()
        self.out: asyncio.Queue = asyncio.Queue()
        self.ws = None
        self.quit = asyncio.Event()
        self.aec = EchoCanceller(aec=settings.audio.aec, noise_suppression=settings.audio.noise_suppression)
        self.player = Player(self.aec, device=settings.audio.output_device, on_done=self._on_playback_done)
        self.listener = Listener(settings, self.aec, self.player, SILERO_VAD, emit=self._emit,
                                 on_wake=self._on_wake)
        self.tray = Tray(self._on_tray)
        self._notifier = None
        self._wake_notification: str | None = None
        self._loop_buf: list[bytes] = []

    # ---- pontes entre threads ------------------------------------------------------------

    def _emit(self, item) -> None:
        self.loop.call_soon_threadsafe(self.out.put_nowait, item)

    def _on_wake(self) -> None:  # thread do listener
        self.player.earcon("wake")
        if settings.wakeword.notify:
            self.loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self._notify_wake()))

    async def _notify_wake(self) -> None:
        # Substitui a notificação da ativação anterior, para não empilhar.
        if self._wake_notification and self._notifier is not None:
            try:
                await self._notifier.clear(self._wake_notification)
            except Exception:
                pass
        self._wake_notification = await self._notify("Jarvis", "Ouvindo…", timeout=4)

    def _on_playback_done(self, utt: str, interrupted: bool) -> None:
        self._emit(PlaybackDone(utterance_id=utt, interrupted=interrupted))

    def _on_tray(self, cmd: str, arg: str | None) -> None:
        if cmd == "quit":
            self.loop.call_soon_threadsafe(self.quit.set)
        else:
            self._emit(UserCommand(command=cmd, arg=arg))

    # ---- execução ------------------------------------------------------------------------

    async def run(self) -> None:
        self.tray.start()
        self.listener.start()
        if self.loopback:
            self.listener.set_open(True)
            log.info("modo loopback: fale e ouça a gravação de volta (Ctrl+C para sair)")
            await asyncio.wait([asyncio.ensure_future(self._loopback()), asyncio.ensure_future(self.quit.wait())],
                               return_when=asyncio.FIRST_COMPLETED)
        else:
            await asyncio.wait([asyncio.ensure_future(self._connect_forever()),
                                asyncio.ensure_future(self.quit.wait())], return_when=asyncio.FIRST_COMPLETED)
        self.listener.stop()
        self.tray.stop()
        self.player.close()

    async def _connect_forever(self) -> None:
        import websockets

        while True:
            try:
                async with websockets.connect(self.core_url, max_size=None, ping_interval=20) as ws:
                    self.ws = ws
                    log.info("conectado ao core em %s", self.core_url)
                    await ws.send(dump(Hello(platform=platform.system().lower(), version=__version__)))
                    sender = asyncio.ensure_future(self._sender(ws))
                    try:
                        await self._receiver(ws)
                    finally:
                        sender.cancel()
            except (OSError, Exception) as e:  # core fora do ar: tenta de novo
                log.debug("sem conexão com o core: %s", e)
            self.ws = None
            self.listener.set_open(False)
            self.player.stop()
            self.tray.update("offline")
            await asyncio.sleep(2.0)

    async def _sender(self, ws) -> None:
        while True:
            item = await self.out.get()
            if isinstance(item, (bytes, bytearray)):
                await ws.send(bytes(item))
            else:
                await ws.send(dump(item))

    async def _receiver(self, ws) -> None:
        async for raw in ws:
            if isinstance(raw, bytes):
                self.player.feed(raw)
                continue
            msg = parse_core(raw)
            match msg:
                case Listen():
                    self.listener.set_open(msg.open)
                case TtsStart():
                    self.player.begin(msg.utterance_id, msg.sample_rate)
                case TtsEnd():
                    self.player.end(msg.utterance_id)
                case StopAudio():
                    self.player.stop()
                case Earcon():
                    self.player.earcon(msg.name)
                case Notify():
                    await self._notify(msg.title, msg.body)
                case State():
                    self.tray.update(msg.state, msg.tasks)

    async def _notify(self, title: str, body: str, timeout: int = -1) -> str | None:
        try:
            if self._notifier is None:
                from desktop_notifier import DesktopNotifier

                self._notifier = DesktopNotifier(app_name="Jarvis")
            ident = await self._notifier.send(title=title, message=body, timeout=timeout)
            log.info("notificação exibida: %s — %s", title, body[:60])
            return ident
        except Exception:
            log.warning("falha ao notificar: %s — %s", title, body, exc_info=True)
            return None

    async def _loopback(self) -> None:
        while True:
            item = await self.out.get()
            if isinstance(item, (bytes, bytearray)):
                self._loop_buf.append(bytes(item))
                continue
            log.info("evento: %s", item)
            if isinstance(item, SpeechEnd):
                audio, self._loop_buf = b"".join(self._loop_buf), []
                self.player.begin("loop", 16000)
                self.player.feed(audio)
                self.player.end("loop")
            elif item.type in ("speech_cancel", "speech_start"):
                self._loop_buf = []


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="jarvis-edge", description="Edge de áudio do Jarvis")
    ap.add_argument("--core", default=f"ws://{settings.link.host}:{settings.link.port}")
    ap.add_argument("--loopback", action="store_true", help="testa mic/VAD/AEC sem o core")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname).1s %(name)s: %(message)s")
    download_edge()

    async def _run():
        await Edge(args.core, loopback=args.loopback).run()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
