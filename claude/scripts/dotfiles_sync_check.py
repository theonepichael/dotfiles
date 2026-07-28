#!/usr/bin/env python3
"""SessionStart hook: flag when the dotfiles repo has drifted from the last
commit bundled over to a GitHub-blocked work machine.

Usage:
    dotfiles_sync_check.py [check]   print a drift note if HEAD is ahead of the marker (default)
    dotfiles_sync_check.py mark [sha]  record the given (or current HEAD) commit as last-bundled
"""

import subprocess
import sys
from pathlib import Path

REPO = Path.home() / "dotfiles"
STATE_DIR = Path.home() / ".local" / "state" / "dotfiles"
MARKER = STATE_DIR / "last-bundled-commit"


def git(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(REPO), *args],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def cmd_check() -> None:
    if not REPO.is_dir() or not MARKER.exists():
        return
    marker_sha = MARKER.read_text().strip()
    if not marker_sha:
        return
    head = git("rev-parse", "HEAD")
    if head is None or head == marker_sha:
        return
    count = git("rev-list", "--count", f"{marker_sha}..{head}")
    if not count or count == "0":
        return
    print(
        f"dotfiles: {count} commit(s) ahead of last bundled transfer ({marker_sha[:7]})"
    )


def cmd_mark(sha: str | None) -> None:
    target = sha or git("rev-parse", "HEAD")
    if not target:
        print("dotfiles_sync_check: could not resolve HEAD", file=sys.stderr)
        sys.exit(1)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    MARKER.write_text(target + "\n")
    print(f"dotfiles: marked {target[:7]} as last-bundled commit")


def main() -> None:
    args = sys.argv[1:]
    subcommand = args[0] if args else "check"
    if subcommand == "check":
        cmd_check()
    elif subcommand == "mark":
        cmd_mark(args[1] if len(args) > 1 else None)
    else:
        print(
            f"dotfiles_sync_check: unknown subcommand {subcommand!r}", file=sys.stderr
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
