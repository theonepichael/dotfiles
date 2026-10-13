"""Shared CLI helpers used across dotfiles scripts.

Flags
  --quiet, -q    suppress non-essential stdout output
  --verbose, -v  emit extra diagnostic messages to stderr

``agent_toolkit_checkout`` locates the agent-toolkit source checkout for the
scripts that read from it, mirroring agent-toolkit's own
``agent-scripts/toolkit_checkout.py`` (minus its own-location step, which
only applies to a script living inside the checkout).
"""

import argparse
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import TextIO

ENV_AGENT_TOOLKIT_PATH = "AGENT_TOOLKIT_PATH"
AGENT_TOOLKIT_MARKERS = ("install.py", "links.toml", "agent-scripts/")


def add_verbosity_args(parser: argparse.ArgumentParser) -> None:
    """Add mutually-exclusive --quiet/-q and --verbose/-v flags to a parser."""
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="suppress non-essential output",
    )
    group.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="emit extra diagnostic messages to stderr",
    )


def vprint(msg: str, *, verbose: bool, file: TextIO | None = None) -> None:
    """Print a diagnostic message when verbose mode is enabled."""
    if verbose:
        if file is None:
            file = sys.stderr
        print(msg, file=file)


def qprint(msg: str, *, quiet: bool, file: TextIO | None = None) -> None:
    """Print a message unless quiet mode is enabled."""
    if not quiet:
        if file is None:
            file = sys.stdout
        print(msg, file=file)


class AgentToolkitNotFoundError(LookupError):
    """No candidate location is an agent-toolkit checkout."""


def is_agent_toolkit_checkout(path: Path) -> bool:
    """True when ``path`` has install.py and links.toml and an agent-scripts/ dir."""
    return (
        (path / "install.py").is_file()
        and (path / "links.toml").is_file()
        and (path / "agent-scripts").is_dir()
    )


def agent_toolkit_checkout(
    *,
    explicit: Path | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return the agent-toolkit checkout, resolved.

    Tries ``explicit``, then ``$AGENT_TOOLKIT_PATH`` (empty counts as
    unset), then the nested ``~/Workspace/agent-toolkit/agent-toolkit``, then
    the flat ``~/Workspace/agent-toolkit``. In the nested layout the flat path
    is the container of the checkout and its worktrees, so it exists but
    fails the marker check. An explicit path or a set env var that is not a
    checkout raises rather than falling through to the defaults.
    """
    env = os.environ if env is None else env
    home = Path.home() if home is None else home
    markers = ", ".join(AGENT_TOOLKIT_MARKERS)

    if explicit is not None:
        if is_agent_toolkit_checkout(explicit):
            return explicit.resolve()
        raise AgentToolkitNotFoundError(
            f"{explicit} is not an agent-toolkit checkout (needs {markers})"
        )

    configured = env.get(ENV_AGENT_TOOLKIT_PATH, "")
    if configured:
        expanded = configured
        if configured == "~" or configured.startswith("~/"):
            expanded = env.get("HOME", str(home)) + configured[1:]
        candidate = Path(expanded)
        if is_agent_toolkit_checkout(candidate):
            return candidate.resolve()
        raise AgentToolkitNotFoundError(
            f"${ENV_AGENT_TOOLKIT_PATH} is set to {configured!r}, which is not "
            f"an agent-toolkit checkout (needs {markers})"
        )

    candidates = (
        home / "Workspace" / "agent-toolkit" / "agent-toolkit",
        home / "Workspace" / "agent-toolkit",
    )
    for candidate in candidates:
        if is_agent_toolkit_checkout(candidate):
            return candidate.resolve()
    tried = ", ".join(str(c) for c in candidates)
    raise AgentToolkitNotFoundError(
        f"no agent-toolkit checkout found (tried ${ENV_AGENT_TOOLKIT_PATH} "
        f"(unset), {tried}); set ${ENV_AGENT_TOOLKIT_PATH} to your checkout"
    )
