"""Ações de desktop por plataforma: Linux nativo ou WSL (agindo no WSL e no Windows)."""

from __future__ import annotations

import functools
from pathlib import Path

from .base import Desktop


def is_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except OSError:
        return False


@functools.cache
def detect() -> Desktop:
    if is_wsl():
        from .wsl import WslDesktop

        return WslDesktop()
    from .linux import LinuxDesktop

    return LinuxDesktop()
