"""Registro de projetos: apelidos falados → diretórios.

Fontes: `~/.config/jarvis/projects.toml` (apelidos explícitos) e a descoberta automática de
repositórios git em `projects_root` (ex.: `~/dev/*`). O projeto atual persiste entre sessões.
"""

from __future__ import annotations

import difflib
import json
import tomllib
from pathlib import Path

from ..config import CONFIG_DIR, DATA_DIR
from .addressing import normalize

PROJECTS_FILE = CONFIG_DIR / "projects.toml"
STATE_FILE = DATA_DIR / "state.json"


def _key(name: str) -> str:
    return normalize(name).replace(" ", "").replace("_", "").replace("-", "")


class ProjectRegistry:
    def __init__(self, roots: list[Path], projects_file: Path = PROJECTS_FILE, state_file: Path = STATE_FILE):
        self.roots = roots
        self.projects_file = projects_file
        self.state_file = state_file
        self.aliases: dict[str, Path] = {}
        self.current: Path | None = None
        self.reload()

    def reload(self) -> None:
        found: dict[str, Path] = {}
        for root in self.roots:
            root = root.expanduser()
            if root.is_dir():
                for d in sorted(root.iterdir()):
                    if (d / ".git").exists():
                        found[d.name] = d
        if self.projects_file.is_file():
            data = tomllib.loads(self.projects_file.read_text())
            for alias, path in data.get("projects", {}).items():
                found[alias] = Path(path).expanduser()
        self.aliases = found
        if self.state_file.is_file():
            cur = json.loads(self.state_file.read_text()).get("current_project")
            self.current = Path(cur) if cur and Path(cur).is_dir() else None

    def resolve(self, spoken: str) -> Path | None:
        """Resolve o nome falado ("projeto jarvis", "o portal do cliente") para um diretório."""
        p = Path(spoken).expanduser()
        if p.is_absolute() and p.is_dir():
            return p
        target = _key(spoken.removeprefix("projeto ").removeprefix("o ").removeprefix("a "))
        if not target:
            return None
        keys = {_key(a): path for a, path in self.aliases.items()}
        if target in keys:
            return keys[target]
        match = difflib.get_close_matches(target, list(keys), n=1, cutoff=0.7)
        return keys[match[0]] if match else None

    def set_current(self, path: Path) -> None:
        self.current = path
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps({"current_project": str(path)}))

    def add(self, alias: str, path: Path) -> None:
        data = tomllib.loads(self.projects_file.read_text()) if self.projects_file.is_file() else {}
        projects = data.get("projects", {})
        projects[alias] = str(path.expanduser().resolve())
        self.projects_file.parent.mkdir(parents=True, exist_ok=True)
        lines = ["[projects]"] + [f'{json.dumps(k)} = {json.dumps(v)}' for k, v in sorted(projects.items())]
        self.projects_file.write_text("\n".join(lines) + "\n")
        self.reload()

    def names(self) -> list[str]:
        return sorted(self.aliases)
