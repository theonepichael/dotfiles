#!/usr/bin/env python3
"""
watchcommit — polls ~/dotfiles every 90 s, auto-commits changes with a
Claude-generated conventional commit message, and pushes to the remote.

Usage:
    watchcommit              # watch ~/dotfiles (default)
    watchcommit /other/repo  # watch a different repo

Pause mechanism: if the touch-file at $XDG_STATE_HOME/watchcommit/paused (or
~/.local/state/watchcommit/paused if XDG_STATE_HOME is unset) exists, the
poll loop skips the commit-and-push cycle for that tick. The shell helpers
wc-pause / wc-resume / wc-status (in dotfiles/zsh/.common_shell_aliases)
manage the touch-file, so manual git history surgery (commit --amend,
rebase, etc.) can block the daemon without resorting to SIGSTOP. The
tick-skipped status is logged via stderr so `journalctl --user -u watchcommit`
shows the gap.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

POLL_INTERVAL = 90
MODEL = "haiku"
PAUSE_FILE = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))) / "watchcommit" / "paused"
SYSTEM_PROMPT = (
    "You are a git commit message generator. "
    "Given a diff, output a single conventional commit message and nothing else.\n\n"
    "Format: type(scope): description\n"
    "Types: feat, fix, refactor, chore, docs, test, perf, ci\n"
    "Rules: present tense, lowercase, no trailing period, max 72 chars total.\n"
    "Output ONLY the commit message — no quotes, no explanation, no extra text."
)


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
    )


def has_changes(repo: Path) -> bool:
    return bool(git(repo, "status", "--porcelain").stdout.strip())


def has_unpushed_commits(repo: Path) -> bool:
    """True if HEAD has commits its upstream doesn't — the state a stalled
    push leaves behind: working tree clean, so has_changes() is False, but
    there's still a local commit sitting there waiting to go out."""
    result = git(repo, "rev-list", "@{u}..HEAD")
    return bool(result.stdout.strip())


def build_diff(repo: Path) -> str:
    parts: list[str] = []

    unstaged = git(repo, "diff").stdout
    staged = git(repo, "diff", "--cached").stdout
    if unstaged:
        parts.append(unstaged)
    if staged:
        parts.append(staged)

    status_lines = git(repo, "status", "--porcelain").stdout.splitlines()
    untracked = [line[3:] for line in status_lines if line.startswith("??")]
    if untracked:
        parts.append("New untracked files:\n" + "\n".join(untracked))

    return "\n".join(parts).strip()


def generate_message(diff: str) -> str:
    result = subprocess.run(
        [
            "claude",
            "--print",
            "--system-prompt", SYSTEM_PROMPT,
            "--model", MODEL,
            "--output-format", "text",
            "--no-session-persistence",
            f"Diff:\n\n{diff}",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude CLI failed: {result.stderr.strip()}")
    return result.stdout.strip()


def current_branch(repo: Path) -> str:
    return git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def rebase_onto_remote(repo: Path) -> bool:
    """Fetch and rebase local commits onto the remote branch, so `push`
    fast-forwards even if another machine's watchcommit pushed in between.
    Returns False on a real conflict — the rebase is aborted immediately
    (no auto-resolution attempted) and local state is left exactly as it
    was, so the commit stays local and gets retried next tick."""
    fetch = git(repo, "fetch", "origin")
    if fetch.returncode != 0:
        print(f"[watchcommit] fetch failed: {fetch.stderr.strip()}", file=sys.stderr)
        return False

    branch = current_branch(repo)
    result = git(repo, "rebase", f"origin/{branch}")
    if result.returncode != 0:
        git(repo, "rebase", "--abort")
        print(
            f"[watchcommit] rebase onto origin/{branch} conflicted — aborted, "
            "local state untouched. Resolve manually (e.g. git pull --rebase) "
            "before the next push will succeed.",
            file=sys.stderr,
        )
        return False
    return True


def push(repo: Path, success_message: str) -> None:
    """Rebase onto the remote, then push. Safe to call whenever there are
    local commits not yet on the remote, regardless of what put them there."""
    if not rebase_onto_remote(repo):
        return
    result = git(repo, "push")
    if result.returncode != 0:
        print(f"[watchcommit] push failed: {result.stderr.strip()}", file=sys.stderr)
    else:
        print(f"[watchcommit] {success_message}")


def commit_and_push(repo: Path, message: str) -> None:
    git(repo, "add", "-A")

    result = git(repo, "commit", "-m", message)
    if result.returncode != 0:
        print(f"[watchcommit] commit failed: {result.stderr.strip()}", file=sys.stderr)
        return

    push(repo, message)


def main() -> None:
    repo = Path(sys.argv[1]).expanduser().resolve() if len(sys.argv) > 1 else Path.home() / "dotfiles"

    if not (repo / ".git").exists():
        print(f"[watchcommit] not a git repo: {repo}", file=sys.stderr)
        sys.exit(1)

    print(f"[watchcommit] watching {repo} every {POLL_INTERVAL}s  (Ctrl-C to stop)")

    while True:
        try:
            if PAUSE_FILE.exists():
                # Pause guard — the touch-file is managed by wc-pause /
                # wc-resume shell helpers (or a manual `touch`/`rm`). Skipping
                # the cycle here holds the daemon back from racing any manual
                # git history surgery. Logged via stderr so journalctl shows
                # the gap alongside the regular commit lines.
                print(f"[watchcommit] paused ({PAUSE_FILE})", file=sys.stderr)
            elif has_changes(repo):
                diff = build_diff(repo)
                if diff:
                    message = generate_message(diff)
                    commit_and_push(repo, message)
            elif has_unpushed_commits(repo):
                # Working tree is clean, but a prior cycle committed and then
                # failed to push (remote had diverged in between). Retry —
                # this is the case that used to get stuck silently forever,
                # since has_changes() is False here and the old loop did
                # nothing at all in that state.
                push(repo, "pushed previously-stuck commit(s)")
            else:
                # Nothing local to protect, so a plain fast-forward pull is
                # always safe here — can only fast-forward or no-op, never
                # conflict. Keeps the working copy current even on machines
                # that only ever read this repo.
                result = git(repo, "pull", "--ff-only")
                if result.returncode != 0:
                    print(f"[watchcommit] background sync failed: {result.stderr.strip()}", file=sys.stderr)
        except KeyboardInterrupt:
            print("\n[watchcommit] stopped")
            sys.exit(0)
        except Exception as exc:
            print(f"[watchcommit] error: {exc}", file=sys.stderr)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
