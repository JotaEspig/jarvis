"""Ações de desktop no WSL: age dentro do WSL e no Windows (via interop `powershell.exe`)."""

from __future__ import annotations

import os
import re
from pathlib import Path

from .base import Desktop, MediaAction, Side, run, spawn, which
from .linux import LinuxDesktop

_WIN_PATH = re.compile(r"^[a-zA-Z]:[\\/]")
# Teclas de mídia virtuais do Windows (VK_MEDIA_*), enviadas via WScript.Shell.
_MEDIA_KEYS = {"play_pause": 179, "next": 176, "previous": 177, "stop": 178}


def _ps_quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


class WslDesktop(Desktop):
    name = "wsl"

    def __init__(self) -> None:
        self._linux = LinuxDesktop()
        self._ps = which("powershell.exe") or "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"

    async def _powershell(self, script: str) -> tuple[int, str]:
        return await run(self._ps, "-NoProfile", "-NonInteractive", "-Command", script)

    async def _to_windows_path(self, target: str) -> str:
        if _WIN_PATH.match(target) or target.startswith(("http://", "https://", "\\\\")):
            return target
        code, out = await run("wslpath", "-w", os.path.expanduser(target))
        return out if code == 0 else target

    async def open_path(self, target: str, side: Side = "auto") -> str:
        # O desktop "de verdade" no WSL é o Windows: por padrão abre lá (inclusive arquivos do WSL).
        if side == "linux" and which("xdg-open"):
            return await self._linux.open_path(target)
        if not (_WIN_PATH.match(target) or target.startswith(("http://", "https://"))):
            if not Path(os.path.expanduser(target)).exists():
                return f"não encontrei {target}"
        win = await self._to_windows_path(target)
        code, out = await self._powershell(f"Start-Process {_ps_quote(win)}")
        return f"abrindo {win} no Windows" if code == 0 else f"falhou: {out}"

    async def open_app(self, app: str, side: Side = "auto") -> str:
        if side == "linux":
            return await self._linux.open_app(app)
        code, out = await self._powershell(f"Start-Process {_ps_quote(app)}")
        if code == 0:
            return f"abrindo {app} no Windows"
        # Apps da Store / atalhos do menu Iniciar: procura pelo nome.
        script = (
            f"$a = Get-StartApps | Where-Object {{ $_.Name -like {_ps_quote('*' + app + '*')} }} | Select-Object -First 1; "
            "if ($a) { Start-Process ('shell:AppsFolder\\' + $a.AppID); $a.Name } else { exit 1 }"
        )
        code, out = await self._powershell(script)
        if code == 0:
            return f"abrindo {out} no Windows"
        if which(app):
            await spawn(app)
            return f"abrindo {app} no WSL"
        return f"não encontrei o app {app}"

    async def media(self, action: MediaAction) -> str:
        key = _MEDIA_KEYS[action]
        code, out = await self._powershell(f"(New-Object -ComObject WScript.Shell).SendKeys([char]{key})")
        return "ok" if code == 0 else out

    def describe(self) -> str:
        return (
            "WSL no Windows. Arquivos do Windows ficam em /mnt/c/... Para abrir arquivos/apps no Windows use "
            "as tools do servidor `desktop` (side=windows); comandos Linux rodam no WSL."
        )
