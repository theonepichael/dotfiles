"""Every Claude command file must be linked out, or it silently never loads.

A skill can be written, reviewed, committed and merged with a fully green suite
while never reaching `~/.claude/commands/`, because nothing connected the file's
existence to a `links.toml` entry. The failure is invisible: the command simply
does not exist at the prompt, with no error anywhere to explain why.

`test_agents_md_links.py` covers AGENTS.md symlinks and `test_pi_extension_links.py`
covers pi extensions. This closes the same gap for `claude/commands/`, for every
future skill rather than for the one that exposed it.
"""

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _linked_sources() -> set[str]:
    data = tomllib.loads((REPO_ROOT / "links.toml").read_text(encoding="utf-8"))
    return {str(link["src"]) for link in data.get("link", []) if "src" in link}


def test_every_claude_command_has_a_links_entry() -> None:
    commands = sorted(
        f"claude/commands/{p.name}"
        for p in (REPO_ROOT / "claude" / "commands").glob("*.md")
    )
    assert commands, "no claude/commands/*.md found -- the glob is wrong"
    missing = sorted(set(commands) - _linked_sources())
    assert not missing, (
        "these command files have no links.toml entry, so they exist in the repo "
        f"but never load in the live harness: {missing}"
    )


def test_every_linked_command_source_exists() -> None:
    """The other direction: a link pointing at a deleted file is dead config."""
    linked = {s for s in _linked_sources() if s.startswith("claude/commands/")}
    dangling = sorted(s for s in linked if not (REPO_ROOT / s).is_file())
    assert not dangling, f"links.toml points at missing command files: {dangling}"
