"""Classificação de risco das ferramentas do worker (`can_use_tool`).

- "allow": segue sem perguntar (leitura, edição dentro do projeto, comandos comuns).
- "ask": precisa de aprovação por voz (destrutivo, externo, irreversível, fora do projeto).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

Verdict = Literal["allow", "ask"]

_READ_ONLY = {
    "Read", "Glob", "Grep", "LS", "WebSearch", "WebFetch", "TodoWrite", "Task", "Agent",
    "NotebookRead", "ExitPlanMode", "BashOutput", "KillShell", "KillBash", "Skill", "ToolSearch",
}
_FILE_WRITE = {"Edit", "Write", "MultiEdit", "NotebookEdit"}

_RISKY_BASH = [
    (r"\brm\s+(-\w*[rRf]\w*\s+|--recursive|--force)", "apagar arquivos"),
    (r"\bgit\s+push\b", "git push"),
    (r"\bgit\s+reset\s+--hard\b", "git reset --hard"),
    (r"\bgit\s+clean\s+-\w*f", "git clean"),
    (r"\bgit\s+(checkout|restore)\s+(--\s+)?\.(\s|$)", "descartar alterações"),
    (r"\bgit\s+branch\s+-D\b", "apagar branch"),
    (r"\bgit\s+rebase\b", "git rebase"),
    (r"--force\b|\s-f\s.*\bpush\b", "operação forçada"),
    (r"\bsudo\b|\bdoas\b", "sudo"),
    (r"\b(dd|mkfs\S*|fdisk|parted|shred)\b", "comando de disco"),
    (r"\bch(mod|own)\s+-R\b", "permissões recursivas"),
    (r"\b(shutdown|reboot|poweroff|halt)\b", "desligar a máquina"),
    (r"\bcurl\b.*(-X\s*(POST|PUT|PATCH|DELETE)|--data|\s-d\s|-F\s)", "requisição HTTP de escrita"),
    (r"\bwget\b.*--post", "requisição HTTP de escrita"),
    (r"\|\s*(ba|z)?sh\b", "executar script baixado"),
    (r"\bdocker\s+(rm|rmi|system\s+prune|volume\s+rm)\b", "remover containers/imagens"),
    (r"\bkubectl\s+(delete|apply|scale|drain)\b", "alterar cluster"),
    (r"\b(npm|pnpm|yarn)\s+publish\b|\btwine\s+upload\b|\bcargo\s+publish\b", "publicar pacote"),
    (r"\bgh\s+(pr\s+(merge|close)|release|repo\s+delete|issue\s+close)\b", "ação no GitHub"),
    (r"\b(drop\s+(table|database)|truncate\s+table|delete\s+from)\b", "SQL destrutivo"),
    (r"\b(powershell(\.exe)?|pwsh|cmd\.exe)\b", "executar no Windows"),
    (r"\bkill(all)?\s+-9\b|\bpkill\b", "matar processos"),
    (r"\bsystemctl\s+(stop|disable|mask|restart)\b", "alterar serviços"),
]
_RISKY_BASH_RE = [(re.compile(p, re.I), why) for p, why in _RISKY_BASH]

_MCP_WRITE_VERBS = re.compile(
    r"(send|delete|remove|trash|create|update|modify|write|post|upload|move|rename|forward|"
    r"respond|reply|merge|publish|cancel|set_|batch|copy|share|invite|transfer|pay)", re.I
)
_SAFE_MCP_SERVERS = {"jarvis", "desktop"}


def _inside(path: str, roots: list[Path]) -> bool:
    try:
        p = Path(path).expanduser().resolve()
    except (OSError, ValueError):
        return False
    return any(p == r or r in p.parents for r in roots)


def classify(tool: str, args: dict, roots: list[Path]) -> tuple[Verdict, str]:
    """Retorna (veredito, motivo curto e falável)."""
    if tool in _READ_ONLY:
        return "allow", ""
    if tool in _FILE_WRITE:
        path = args.get("file_path") or args.get("notebook_path") or ""
        if roots and _inside(path, roots):
            return "allow", ""
        return "ask", f"alterar um arquivo fora do projeto: {Path(path).name or path}"
    if tool == "Bash":
        cmd = args.get("command", "")
        for rx, why in _RISKY_BASH_RE:
            if rx.search(cmd):
                return "ask", why
        return "allow", ""
    if tool.startswith("mcp__"):
        _, server, name = (tool.split("__", 2) + ["", ""])[:3]
        if server in _SAFE_MCP_SERVERS:
            return "allow", ""
        if _MCP_WRITE_VERBS.search(name):
            return "ask", f"{name.replace('_', ' ')} via {server}"
        return "allow", ""
    return "ask", f"usar a ferramenta {tool}"
