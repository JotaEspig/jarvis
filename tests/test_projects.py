from jarvis.core.projects import ProjectRegistry


def _make(tmp_path):
    root = tmp_path / "dev"
    for name in ("jarvis", "portal-cliente", "api_pagamentos"):
        (root / name / ".git").mkdir(parents=True)
    return ProjectRegistry([root], projects_file=tmp_path / "p.toml", state_file=tmp_path / "s.json"), root


def test_resolve_spoken_names(tmp_path):
    reg, root = _make(tmp_path)
    assert reg.resolve("jarvis") == root / "jarvis"
    assert reg.resolve("projeto jarvis") == root / "jarvis"
    assert reg.resolve("portal cliente") == root / "portal-cliente"
    assert reg.resolve("api de pagamentos") is None or reg.resolve("api pagamentos") == root / "api_pagamentos"
    assert reg.resolve("inexistente") is None


def test_alias_and_current_persist(tmp_path):
    reg, root = _make(tmp_path)
    reg.add("site", root / "portal-cliente")
    reg.set_current(root / "jarvis")
    reg2 = ProjectRegistry([root], projects_file=tmp_path / "p.toml", state_file=tmp_path / "s.json")
    assert reg2.resolve("site") == root / "portal-cliente"
    assert reg2.current == root / "jarvis"
