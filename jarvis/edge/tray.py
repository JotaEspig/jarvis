"""Ícone na bandeja com o estado do assistente e as tarefas (opcional: falha silenciosamente)."""

from __future__ import annotations

import logging
import os
import sys
from typing import Callable

log = logging.getLogger(__name__)

_COLORS = {
    "offline": (120, 120, 120),
    "idle": (60, 90, 160),
    "listening": (40, 170, 90),
    "thinking": (230, 170, 30),
    "speaking": (60, 160, 230),
}


class Tray:
    def __init__(self, on_command: Callable[[str, str | None], None]):
        self._on_command = on_command
        self._icon = None
        self._state = "offline"
        self._tasks: list = []

    def start(self) -> None:
        logging.getLogger("pystray").setLevel(logging.CRITICAL)
        if sys.platform == "linux" and os.environ.get("WAYLAND_DISPLAY"):
            try:  # Wayland (Hyprland/waybar, KDE, GNOME c/ extensão): tray via StatusNotifierItem
                import gi  # noqa: F401

                os.environ.setdefault("PYSTRAY_BACKEND", "appindicator")
            except ImportError:
                log.info("bandeja: instale o extra `tray` (PyGObject) para o tray no Wayland")
        try:
            import pystray

            self._pystray = pystray
            self._icon = pystray.Icon("jarvis", self._image(), "Jarvis", menu=self._menu())
            self._icon.run_detached()
        except Exception as e:  # sem bandeja disponível (ex.: Wayland sem appindicator)
            log.info("bandeja indisponível: %s", e)
            self._icon = None

    def _image(self):
        from PIL import Image, ImageDraw

        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        ImageDraw.Draw(img).ellipse((6, 6, 58, 58), fill=_COLORS.get(self._state, _COLORS["idle"]))
        return img

    def _menu(self):
        p = self._pystray
        items = [p.MenuItem(f"Jarvis — {self._state}", None, enabled=False)]
        for t in self._tasks:
            items.append(p.MenuItem(f"#{t.id} {t.status}: {t.title[:50]}", None, enabled=False))
        items += [
            p.Menu.SEPARATOR,
            p.MenuItem("Encerrar conversa", lambda: self._on_command("end_conversation", None)),
            p.MenuItem("Sair", lambda: self._on_command("quit", None)),
        ]
        return p.Menu(*items)

    def update(self, state: str, tasks: list | None = None) -> None:
        self._state = state
        if tasks is not None:
            self._tasks = tasks
        if self._icon is None:
            return
        try:
            self._icon.icon = self._image()
            self._icon.title = f"Jarvis — {state}"
            self._icon.menu = self._menu()
            self._icon.update_menu()
        except Exception:
            log.debug("falha ao atualizar a bandeja", exc_info=True)

    def stop(self) -> None:
        if self._icon is not None:
            self._icon.stop()
