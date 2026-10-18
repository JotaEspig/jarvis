"""Classificação de risco das ferramentas do worker (`can_use_tool`).

- "allow": segue sem perguntar (leitura, edição dentro do projeto, comandos comuns).
- "ask": precisa de aprovação por voz (destrutivo, externo, irreversível, fora do projeto).
- "deny": proibido. Exclusão definitiva de arquivos nunca acontece; o máximo permitido é mandar
  para a lixeira com o `trash-put` (trash-cli), e mesmo assim com confirmação por voz.
"""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path
from typing import Literal

Verdict = Literal["allow", "ask", "deny"]

TRASH_HINT = ("Exclusão definitiva é proibida neste ambiente. Para remover arquivos use `trash-put <caminhos>` "
              "(vai para a lixeira e pode ser restaurado com trash-restore); isso pede confirmação do usuário por voz.")

_READ_ONLY = {
    "Read", "Glob", "Grep", "LS", "WebSearch", "WebFetch", "TodoWrite", "Task", "Agent",
    "NotebookRead", "ExitPlanMode", "BashOutput", "KillShell", "KillBash", "Skill", "ToolSearch",
}
_FILE_WRITE = {"Edit", "Write", "MultiEdit", "NotebookEdit"}

# Exclusão definitiva: PROIBIDA (o worker é orientado a usar o trash-put).
_PERMANENT_DELETE = [
    r"(^|[\s;&|(`$])(?<!git )(sudo\s+)?(rm|rmdir|unlink|shred|srm|wipe)\b(?!-)",
    r"\bfind\b.*\s-(delete|exec(dir)?\s+(rm|unlink|shred))\b",
    r"\bxargs\b.*\b(rm|unlink|shred)\b",
    r"\b(os\.(remove|unlink|rmdir|removedirs)|shutil\.rmtree|\.unlink\(|\.rmdir\(|fs\.(rm|unlink|rmdir)(Sync)?"
    r"|\b(unlink|rm|rmdir)Sync\(|rimraf|FileUtils\.rm)",
    r"\bgit\s+clean\b",
    r"\bmv\b.*\s/dev/null\b",
    r"\btrash-(empty|rm)\b|\bgio\s+remove\b",
    r"\bRemove-Item\b|\bcmd(\.exe)?\s+/c\s+(del|erase|rd|rmdir)\b",
]
_PERMANENT_DELETE_RE = [re.compile(p, re.I) for p in _PERMANENT_DELETE]

# Exclusões recuperáveis (lixeira / histórico do git) ou perda de trabalho: confirmação por voz.
_DELETE_BASH = [
    (r"\btrash(-put)?(?![\w-])|\bgio\s+trash\b", "mandar arquivos para a lixeira"),
    (r"\bmv\b.*(\.Trash|\.local/share/Trash|\blixo\b|\btrash\b)", "mandar arquivos para a lixeira"),
    (r"\btruncate\s", "zerar arquivos"),
    (r"\bgit\s+rm\b", "remover arquivos do git"),
    (r"\bgit\s+(checkout\s+--|restore\b(?!.*--staged))", "descartar alterações não salvas"),
    (r"\bgit\s+reset\s+--hard\b", "descartar alterações com reset --hard"),
    (r"\bgit\s+stash\s+(drop|clear)\b", "apagar stash"),
    (r"\bgit\s+branch\s+-[dD]\b|\bgit\s+push\b.*(--delete|\s:\S)", "apagar branch"),
]
_DELETE_BASH_RE = [(re.compile(p, re.I), why) for p, why in _DELETE_BASH]

_RISKY_BASH = [
    (r"\bgit\s+push\b", "git push"),
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


# Comandos que alteram o sistema de arquivos: os caminhos que eles tocam precisam estar no projeto.
_FS_WRITERS = {
    "mv", "cp", "rm", "rmdir", "mkdir", "touch", "ln", "chmod", "chown", "rsync", "tee", "truncate",
    "install", "unzip", "tar", "sed", "trash", "trash-put", "gio", "find", "rename", "dd", "shred",
}
_SHELL_KEYWORDS = {"do", "then", "else", "{", "(", "time", "nohup", "env", "command", "exec"}
_SEGMENT_SPLIT = re.compile(r"&&|\|\||;|\||\n")
_REDIRECT = re.compile(r"(?<![0-9&])>>?\s*([^\s;&|]+)")
_ALWAYS_WRITABLE = [Path("/tmp"), Path("/dev/null")]


def _resolve(arg: str, cwd: Path) -> Path:
    arg = os.path.expandvars(os.path.expanduser(arg.replace("${HOME}", "~").replace("$HOME", "~")))
    p = Path(arg)
    return (p if p.is_absolute() else cwd / p).resolve()


def _bash_outside_writes(cmd: str, roots: list[Path]) -> str | None:
    """Primeiro caminho fora do projeto que o comando alteraria, ou None.

    Conservador: segue `cd`, considera argumentos com variáveis ($d) relativos ao cwd corrente
    e trata redirecionamentos (> arquivo) como escrita.
    """
    if not roots:
        return None
    writable = roots + _ALWAYS_WRITABLE
    cwd = roots[0]
    for m in _REDIRECT.finditer(cmd):
        target = _resolve(m.group(1), cwd)
        if not _inside(str(target), writable):
            return str(target)
    for seg in _SEGMENT_SPLIT.split(cmd):
        try:
            words = shlex.split(seg, comments=True)
        except ValueError:
            words = seg.split()
        while words and (words[0] in _SHELL_KEYWORDS or "=" in words[0].split("/")[0] and not words[0].startswith("-")):
            words = words[1:]
        if not words:
            continue
        verb, args = os.path.basename(words[0]), words[1:]
        if verb == "cd":
            cwd = _resolve(args[0], cwd) if args else Path.home()
            continue
        if verb == "git" and args and args[0] in ("mv", "rm", "clean", "checkout", "restore", "reset", "stash", "init", "clone"):
            paths = [a for a in args[1:] if not a.startswith("-")] or ["."]
        elif verb in _FS_WRITERS:
            if verb == "sed" and not any(a.startswith("-i") or a == "--in-place" for a in args):
                continue
            if verb == "find" and not any(a in ("-delete", "-exec", "-execdir") for a in args):
                continue
            if verb == "tar" and not any(re.match(r"^-?[a-zA-Z]*[xc]", a) for a in args[:1]):
                continue
            paths = [a for a in args if not a.startswith("-")] or ["."]
        else:
            continue
        for a in paths:
            target = _resolve(a, cwd)
            if not _inside(str(target), writable):
                return str(target)
    return None


def _inside(path: str, roots: list[Path]) -> bool:
    try:
        p = Path(path).expanduser().resolve()
    except (OSError, ValueError):
        return False
    return any(p == r or r in p.parents for r in roots)


def _targets(cmd: str, rx: re.Pattern) -> str:
    """Resumo falável do alvo: só os argumentos do trecho perigoso (sem o resto do comando)."""
    seg = next((x for x in _SEGMENT_SPLIT.split(cmd) if rx.search(x)), cmd)
    try:
        words = shlex.split(seg)
    except ValueError:
        words = seg.split()
    args = [os.path.basename(w.rstrip("/")) or w for w in words[1:] if not w.startswith("-")]
    if words[:1] == ["git"]:
        args = args[1:]
    if not args:
        return seg.strip()[:60]
    extra = f" e mais {len(args) - 3}" if len(args) > 3 else ""
    return ", ".join(args[:3]) + extra


def _speakable(path: str) -> str:
    home = str(Path.home())
    return path.replace(home, "sua home", 1) if path.startswith(home) else path


def classify(tool: str, args: dict, roots: list[Path]) -> tuple[Verdict, str]:
    """Retorna (veredito, motivo curto e falável)."""
    if tool in _READ_ONLY:
        return "allow", ""
    if tool in _FILE_WRITE:
        path = args.get("file_path") or args.get("notebook_path") or ""
        if tool == "NotebookEdit" and args.get("edit_mode") == "delete":
            return "ask", f"apagar uma célula de {Path(path).name}"
        if roots and _inside(path, roots):
            return "allow", ""
        return "ask", f"alterar um arquivo fora do projeto: {Path(path).name or path}"
    if tool == "Bash":
        cmd = args.get("command", "")
        if any(rx.search(cmd) for rx in _PERMANENT_DELETE_RE):
            return "deny", TRASH_HINT
        for rx, why in _DELETE_BASH_RE:
            if rx.search(cmd):
                return "ask", f"{why}: {_targets(cmd, rx)}"
        for rx, why in _RISKY_BASH_RE:
            if rx.search(cmd):
                return "ask", why
        outside = _bash_outside_writes(cmd, roots)
        if outside:
            return "ask", f"alterar arquivos fora do projeto, em {_speakable(outside)}"
        return "allow", ""
    if tool.startswith("mcp__"):
        _, server, name = (tool.split("__", 2) + ["", ""])[:3]
        if server in _SAFE_MCP_SERVERS:
            return "allow", ""
        if _MCP_WRITE_VERBS.search(name):
            return "ask", f"{name.replace('_', ' ')} via {server}"
        return "allow", ""
    return "ask", f"usar a ferramenta {tool}"
