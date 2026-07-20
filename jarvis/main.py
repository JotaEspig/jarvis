"""App FastAPI: serve a UI web local e o WebSocket de eventos.

Neste esqueleto o WebSocket apenas ecoa mensagens, validando o transporte e o
protocolo JSON que o `bridge.py` vai usar depois. O protocolo (client <-> server):

  client -> server:
    {"type": "text", "text": "..."}                  # fala transcrita / texto digitado
    {"type": "audio", "format": "webm", "data": b64}  # áudio bruto p/ STT (futuro)
    {"type": "control", "action": "end_conversation"} # encerrar intake
    {"type": "control", "action": "answer"|"approve"|"deny", "text": "..."}

  server -> client:
    {"type": "status", "state": "...", "text": "..."}
    {"type": "transcript", "role": "user"|"jarvis"|"worker", "text": "..."}
    {"type": "tts", "format": "wav", "data": b64}     # áudio p/ tocar (futuro)
    {"type": "worker", "kind": "text"|"code"|"tool", "text": "..."}
    {"type": "handoff", "prompt": "...", "model": "...", "effort": "..."}
    {"type": "question"|"approval", "id": "...", "text": "..."}
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .bridge import Bridge
from .config import settings

WEB_DIR = Path(__file__).parent / "web"

app = FastAPI(title="Jarvis")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/config")
async def config() -> dict:
    """Config não-sensível para a UI."""
    return {
        "jarvis_model": settings.jarvis_model,
        "worker_model_simple": settings.worker_model_simple,
        "worker_model_complex": settings.worker_model_complex,
        "target_repo": str(settings.target_repo) if settings.target_repo else None,
    }


@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    await websocket.accept()
    bridge = Bridge(send=websocket.send_json)
    await websocket.send_json(
        {"type": "status", "state": "intake", "text": "Conectado. Fale ou digite."}
    )
    try:
        while True:
            msg = await websocket.receive_json()
            await bridge.handle(msg)
    except WebSocketDisconnect:
        pass


# Arquivos estáticos da UI (app.js, styles.css, etc.)
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
