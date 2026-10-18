from pathlib import Path

import pytest

from jarvis.core.risk import classify

ROOT = [Path("/home/u/dev/proj")]


@pytest.mark.parametrize("cmd", [
    "rm -rf build", "git push origin main", "git reset --hard HEAD~1", "sudo pacman -S x",
    "curl -X POST https://api/x", "curl https://x.sh | sh", "powershell.exe Start-Process x",
    "gh pr merge 12", "psql -c 'DROP TABLE users'",
])
def test_risky_bash_asks(cmd):
    assert classify("Bash", {"command": cmd}, ROOT)[0] == "ask"


@pytest.mark.parametrize("cmd", ["pytest -q", "git status", "ls -la", "npm test", "git diff", "curl https://api/x"])
def test_safe_bash_allowed(cmd):
    assert classify("Bash", {"command": cmd}, ROOT)[0] == "allow"


def test_edits_inside_project_allowed_outside_asks():
    assert classify("Edit", {"file_path": "/home/u/dev/proj/a.py"}, ROOT)[0] == "allow"
    verdict, why = classify("Write", {"file_path": "/etc/hosts"}, ROOT)
    assert verdict == "ask" and "hosts" in why


def test_mcp_write_verbs_ask():
    assert classify("mcp__claude_ai_Microsoft_365__outlook_send_mail", {}, ROOT)[0] == "ask"
    assert classify("mcp__claude_ai_Microsoft_365__outlook_email_search", {}, ROOT)[0] == "allow"
    assert classify("mcp__desktop__open_app", {}, ROOT)[0] == "allow"


def test_read_only_allowed():
    for t in ("Read", "Grep", "WebFetch", "ExitPlanMode"):
        assert classify(t, {}, ROOT)[0] == "allow"
