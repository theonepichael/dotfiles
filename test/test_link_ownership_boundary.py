"""Guards the dotfiles/agent-toolkit live-symlink ownership boundary.

2026-09-07 incident: dotfiles' links.toml still declared [[link]] rows for
cli_common.py, gen_interfaces.py, and settings_seed_drift_check.py months
after meta-agent-toolkit-migration-cutover handed those destinations to
agent-toolkit permanently -- the 2026-09-04 cutover commit kept dotfiles'
own repo-local copies (install.py imports them from its own checkout, never
via the live symlink) but never dropped the *symlink* rows. Because
install-with-agent-toolkit.sh always runs dotfiles' installer second (to
reassert the genuinely personal-overlay destinations), those stale rows
meant dotfiles silently reclaimed all three scripts back from agent-toolkit
on every wrapper run -- undetected until a live --check-links audit caught
it by hand.

This test makes that class of drift a hard failure instead of a manual
discovery: the only destinations either repo's links.toml may legitimately
both declare are the four personal-overlay ones dotfiles composes with
personal-overlay.md and is *supposed* to keep winning. Any other overlap
means one side's declaration should have been dropped and wasn't.
"""

import os
import tomllib
from pathlib import Path

import pytest

import conftest

REPO_ROOT = Path(__file__).resolve().parent.parent

# The only destinations both repos are allowed to declare -- dotfiles wins
# these by design (install-with-agent-toolkit.sh runs it second specifically
# to reassert them). Every other overlapping destination is drift.
_SANCTIONED_SHARED_DESTS = {
    "~/.claude/CLAUDE.md",
    "~/.copilot/copilot-instructions.md",
    "~/.gemini/GEMINI.md",
    "~/.pi/agent/AGENTS.md",
}


def _agent_toolkit_links_toml() -> Path | None:
    """Same AGENT_TOOLKIT_PATH convention as install-with-agent-toolkit.sh."""
    candidate = (
        Path(
            os.environ.get(
                "AGENT_TOOLKIT_PATH",
                str(conftest._REAL_HOME / "Workspace" / "agent-toolkit"),
            )
        )
        / "links.toml"
    )
    return candidate if candidate.is_file() else None


def _dest_set(links_toml: Path) -> set[str]:
    data = tomllib.loads(links_toml.read_text())
    return {row["dest"] for row in data.get("link", [])}


def test_no_unsanctioned_overlap_between_dotfiles_and_agent_toolkit_links():
    agent_toolkit_links = _agent_toolkit_links_toml()
    if agent_toolkit_links is None:
        pytest.skip(
            "agent-toolkit checkout not found -- set AGENT_TOOLKIT_PATH to "
            "run this cross-repo check"
        )

    dotfiles_dests = _dest_set(REPO_ROOT / "links.toml")
    agent_toolkit_dests = _dest_set(agent_toolkit_links)

    overlap = dotfiles_dests & agent_toolkit_dests
    unsanctioned = overlap - _SANCTIONED_SHARED_DESTS

    assert not unsanctioned, (
        "dotfiles and agent-toolkit both declare a live symlink destination "
        f"outside the sanctioned personal-overlay set: {sorted(unsanctioned)}. "
        "install-with-agent-toolkit.sh always runs dotfiles' installer "
        "second, so dotfiles will silently reclaim these from agent-toolkit "
        "on every run. Either drop the stale [[link]] row from whichever "
        "repo no longer owns that destination, or add it to "
        "_SANCTIONED_SHARED_DESTS above if the overlap is newly deliberate."
    )
