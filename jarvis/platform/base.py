"""Interface das ações de desktop expostas ao worker."""

from __future__ import annotations

import asyncio
import shutil
from typing import Literal

Side = Literal["auto", "linux", "windows"]
MediaAction = Literal["play_pause", "next", "previous", "stop"]


class Desktop:
    name = "base"

    async def open_path(self, target: str, side: Side = "auto") -> str:
        """Abre arquivo, pasta ou URL no app padrão. Retorna uma descrição do que fez."""
        raise NotImplementedError

    async def open_app(self, app: str, side: Side = "auto") -> str:
        raise NotImplementedError

    async def media(self, action: MediaAction) -> str:
        raise NotImplementedError

    def describe(self) -> str:
        """Linha para o prompt do worker explicando o ambiente."""
        raise NotImplementedError


async def run(*cmd: str, timeout: float = 15.0) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, "timeout"
    return proc.returncode or 0, out.decode(errors="replace").strip()


async def spawn(*cmd: str) -> None:
    """Dispara um processo desacoplado (apps GUI não devem segurar o worker)."""
    await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, start_new_session=True
    )


def which(name: str) -> str | None:
    return shutil.which(name)
