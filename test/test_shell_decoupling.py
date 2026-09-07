"""Tests for .zshrc's decoupling from harness-tool sourcing.

shell/agent-tools.zsh itself moved to agent-toolkit's sole ownership
2026-09-07 (meta-agent-toolkit-wrapper-enforcement) -- it's cross-harness
tooling (completions, harness PATH entries, copilot aliases), not personal
config, and nothing in this repo depends on a local copy the way install.py
depends on cli_common.py. .zshrc still sources it by the same fixed
destination regardless of which repo's copy is live-installed, which is
what the remaining test below actually checks.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_zshrc_sources_agent_tools():
    zshrc = (REPO_ROOT / "zsh" / ".zshrc").read_text()
    assert "agent-tools.zsh" in zshrc
    assert ".copilot_aliases" not in zshrc
