#!/usr/bin/env python3
"""opencode_skills_sync — mirrors ~/.config/opencode/skills into a second,
push-free worktree of this dotfiles repo, and commits any change it finds.

Usage:
    opencode_skills_sync.py [source] [dest-worktree]
        source          default: ~/.config/opencode/skills
        dest-worktree   default: ~/dotfiles-opencode-skills

Curated skills (a skill directory that is itself a symlink, or whose
SKILL.md is a symlink -- the existing dotfiles/opencode/skills pattern used
by grill-me, second-opinion, and spec) are skipped: those are already
tracked via links.toml and don't need a raw snapshot. Every other
("uncurated") skill directory is wipe-then-recopied each tick into
<dest-worktree>/opencode/skills-live/<name>/, so a file deleted inside a
surviving skill is correctly gone from the mirror too, not just a
whole-directory deletion.

Commit-only, by construction: no code path in this module ever invokes
`git push`, `git fetch`, or `git pull` -- the mirrored skills live only in a
local branch of the second worktree until someone deliberately promotes one
out. A tick with no actual changes does not create an empty commit.

Pause mechanism: if the touch-file at $XDG_STATE_HOME/opencode-skills-sync/
paused (or ~/.local/state/opencode-skills-sync/paused if XDG_STATE_HOME is
unset) exists, a tick is skipped entirely -- mirrors watchcommit.py's
PAUSE_FILE pattern. No daemon restart needed to toggle.

Env vars: XDG_STATE_HOME (optional, for the pause touch-file location).
Files written: <dest-worktree>/opencode/skills-live/ (mirrored skill
copies), plus git objects/commits inside <dest-worktree>/.git.
Exit codes: the polling loop in main() never exits non-zero on a tick
error -- it logs to stderr and keeps polling; Ctrl-C exits 0.
"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

POLL_INTERVAL = 90
SOURCE_DEFAULT = Path.home() / ".config" / "opencode" / "skills"
DEST_DEFAULT = Path.home() / "dotfiles-opencode-skills"
MIRROR_RELPATH = Path("opencode") / "skills-live"
COMMIT_MESSAGE = "chore(opencode): snapshot live skills"

STATE_DIR = (
    Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state")))
    / "opencode-skills-sync"
)
PAUSE_FILE = STATE_DIR / "paused"


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
    )


def is_curated(entry: Path) -> bool:
    """A skill directory is curated (tracked via links.toml, not
    raw-mirrored) if it is itself a symlink, or its SKILL.md is."""
    return entry.is_symlink() or (entry / "SKILL.md").is_symlink()


def mirror_tick(source: Path, dest_worktree: Path) -> None:
    """One mirror pass: wipe-then-recopy every uncurated skill directory
    found in `source` into <dest_worktree>/opencode/skills-live/, then prune
    any mirrored entry no longer current (deleted from source, or promoted
    to curated). Hard-scoped to that subdirectory by construction -- it only
    ever walks `live_dir`, never dest_worktree itself."""
    live_dir = dest_worktree / MIRROR_RELPATH
    live_dir.mkdir(parents=True, exist_ok=True)

    try:
        entries = list(source.iterdir())
    except FileNotFoundError:
        entries = []

    uncurated_names: set[str] = set()
    for entry in entries:
        if not entry.is_dir():
            continue
        if is_curated(entry):
            continue
        uncurated_names.add(entry.name)
        dest = live_dir / entry.name
        if dest.is_symlink():
            dest.unlink()
        elif dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(entry, dest, symlinks=True)

    for existing in live_dir.iterdir():
        if existing.name not in uncurated_names:
            if existing.is_symlink() or existing.is_file():
                existing.unlink()
            else:
                shutil.rmtree(existing)


def commit_if_changed(dest_worktree: Path) -> bool:
    """Commit any change under dest_worktree with a fixed message -- no LLM
    call needed for an automated raw-dump snapshot nobody reads as narrated
    history. Never calls git push/fetch/pull. Returns True if a commit was
    made."""
    if not git(dest_worktree, "status", "--porcelain").stdout.strip():
        return False
    git(dest_worktree, "add", "-A")
    result = git(dest_worktree, "commit", "-m", COMMIT_MESSAGE)
    return result.returncode == 0


def run_tick(source: Path, dest_worktree: Path) -> bool:
    """One full poll-tick: mirror then commit. Returns True if a commit was
    made."""
    mirror_tick(source, dest_worktree)
    return commit_if_changed(dest_worktree)


def maybe_run_tick(source: Path, dest_worktree: Path) -> bool:
    """run_tick(), unless PAUSE_FILE exists -- mirrors watchcommit.py's
    is_paused() gate. Returns False for a skipped tick."""
    if PAUSE_FILE.exists():
        print(f"[opencode-skills-sync] paused ({PAUSE_FILE})", file=sys.stderr)
        return False
    return run_tick(source, dest_worktree)


def main() -> None:
    source = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else SOURCE_DEFAULT
    dest_worktree = (
        Path(sys.argv[2]).expanduser() if len(sys.argv) > 2 else DEST_DEFAULT
    )

    print(
        f"[opencode-skills-sync] mirroring {source} into {dest_worktree} "
        f"every {POLL_INTERVAL}s  (Ctrl-C to stop)"
    )

    while True:
        try:
            if maybe_run_tick(source, dest_worktree):
                print("[opencode-skills-sync] committed a snapshot")
        except KeyboardInterrupt:
            print("\n[opencode-skills-sync] stopped")
            sys.exit(0)
        except Exception as exc:
            print(f"[opencode-skills-sync] error: {exc}", file=sys.stderr)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
