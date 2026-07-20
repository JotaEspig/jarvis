"""Entrypoint de console: `jarvis` sobe o servidor web local."""

from __future__ import annotations

import webbrowser

import uvicorn

from .config import settings


def main() -> None:
    url = f"http://{settings.host}:{settings.port}"
    print(f"Jarvis rodando em {url}")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    uvicorn.run(
        "jarvis.main:app",
        host=settings.host,
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
