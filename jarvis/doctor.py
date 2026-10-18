"""`jarvis doctor`: confere credencial, latência do Haiku, modelos locais e áudio."""

from __future__ import annotations

import os
import time

OK, WARN, FAIL = "✔", "!", "✘"


def _line(mark: str, what: str, detail: str = "") -> None:
    print(f" {mark} {what}" + (f" — {detail}" if detail else ""))


async def _haiku_latency(model: str) -> tuple[float, float, str]:
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
    from claude_agent_sdk.types import ResultMessage, StreamEvent

    from .core.auth import sdk_env

    opts = ClaudeAgentOptions(model=model, system_prompt="Responda com uma palavra.", tools=[], setting_sources=[],
                              include_partial_messages=True, max_thinking_tokens=0, env=sdk_env(), cwd="/tmp")
    ttfts = []
    text = ""
    async with ClaudeSDKClient(options=opts) as c:
        for q in ("Diga oi.", "Diga tchau."):
            t = time.perf_counter()
            first = None
            await c.query(q)
            async for m in c.receive_response():
                if isinstance(m, StreamEvent) and m.event.get("type") == "content_block_delta" and first is None:
                    first = time.perf_counter() - t
                elif isinstance(m, ResultMessage):
                    text = m.result or ""
                    if m.is_error:
                        raise RuntimeError(text)
            ttfts.append(first or float("nan"))
    return ttfts[0], ttfts[1], text


async def run() -> int:
    from .config import settings
    from .core.auth import harden_environment, resolve_auth
    from .download import KOKORO_MODEL, PARAKEET_DIR, SILERO_VAD
    from .platform import detect

    failed = False
    print("Jarvis doctor\n")
    info = resolve_auth()
    if info.source in ("env", "file"):
        _line(OK, "credencial", f"token do setup-token ({info.source})")
    elif info.source == "cli-login":
        _line(WARN, "credencial", "usando login do Claude Code; prefira `claude setup-token`")
    else:
        _line(FAIL, "credencial", "rode `claude setup-token`")
        return 1
    if os.environ.get("ANTHROPIC_API_KEY"):
        _line(WARN, "ANTHROPIC_API_KEY no ambiente", "será ignorada (o Jarvis remove para não cobrar via API)")
    harden_environment()
    try:
        cold, warm, _ = await _haiku_latency(settings.front.model)
        mark = OK if warm < 2.0 else WARN
        _line(mark, f"front {settings.front.model}", f"1º token: {cold:.2f}s (frio) / {warm:.2f}s (sessão aquecida)")
    except Exception as e:
        failed = True
        _line(FAIL, f"front {settings.front.model}", str(e)[:200])

    for name, path in (("STT Parakeet", PARAKEET_DIR / "tokens.txt"), ("TTS Kokoro", KOKORO_MODEL),
                       ("VAD Silero", SILERO_VAD)):
        _line(OK if path.exists() else WARN, name, "ok" if path.exists() else "não baixado (`jarvis download`)")

    try:
        import sounddevice as sd

        _line(OK, "áudio", f"entrada: {sd.query_devices(kind='input')['name']} · "
                           f"saída: {sd.query_devices(kind='output')['name']}")
    except Exception as e:
        _line(WARN, "áudio", f"sem dispositivo local ({e}); normal no WSL com o edge no Windows")
    _line(OK, "plataforma", detect().describe())
    return 1 if failed else 0
