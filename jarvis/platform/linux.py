"""Ações de desktop no Linux nativo (xdg-open, .desktop, playerctl)."""

from __future__ import annotations

import difflib
import os
from pathlib import Path

from .base import Desktop, MediaAction, Side, run, spawn, which

_APP_DIRS = [
    Path.home() / ".local/share/applications",
    Path("/usr/share/applications"),
    Path("/var/lib/flatpak/exports/share/applications"),
    Path.home() / ".local/share/flatpak/exports/share/applications",
]


def desktop_entries() -> dict[str, str]:
    """Nome visível (minúsculo) → id do .desktop."""
    entries: dict[str, str] = {}
    for d in _APP_DIRS:
        if not d.is_dir():
            continue
        for f in d.glob("*.desktop"):
            name = None
            try:
                for line in f.read_text(errors="ignore").splitlines():
                    if line.startswith("Name="):
                        name = line[5:].strip().lower()
                        break
            except OSError:
                continue
            entries.setdefault(name or f.stem.lower(), f.stem)
            entries.setdefault(f.stem.lower(), f.stem)
    return entries


class LinuxDesktop(Desktop):
    name = "linux"

    async def open_path(self, target: str, side: Side = "auto") -> str:
        if not target.startswith(("http://", "https://")):
            target = os.path.expanduser(target)
            if not Path(target).exists():
                return f"não encontrei {target}"
        await spawn("xdg-open", target)
        return f"abrindo {target}"

    async def open_app(self, app: str, side: Side = "auto") -> str:
        entries = desktop_entries()
        key = app.lower().strip()
        match = entries.get(key) or next(
            (entries[k] for k in difflib.get_close_matches(key, list(entries), n=1, cutoff=0.6)), None
        )
        if match and which("gtk-launch"):
            await spawn("gtk-launch", match)
            return f"abrindo {match}"
        if which(key):
            await spawn(key)
            return f"abrindo {key}"
        return f"não encontrei o app {app}"

    async def media(self, action: MediaAction) -> str:
        if not which("playerctl"):
            return "playerctl não está instalado"
        cmd = {"play_pause": "play-pause", "next": "next", "previous": "previous", "stop": "stop"}[action]
        code, out = await run("playerctl", cmd)
        return "ok" if code == 0 else out or "nenhum player ativo"

    def describe(self) -> str:
        return "Linux nativo (desktop local)."
