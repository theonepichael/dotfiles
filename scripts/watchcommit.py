#!/usr/bin/env -S uv run --quiet
# /// script
# requires-python = ">=3.13"
# dependencies = ["anthropic>=0.50.0"]
# ///
"""
watchcommit — polls ~/dotfiles every 90 s, auto-commits changes with a
Claude-generated conventional commit message, and pushes to the remote.

Usage:
    watchcommit              # watch ~/dotfiles (default)
    watchcommit /other/repo  # watch a different repo
"""

import subprocess
import sys
import time
from pathlib import Path

import anthropic

POLL_INTERVAL = 90
MODEL = "claude-haiku-4-5"
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
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=128,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": f"Diff:\n\n{diff}"}],
    )
    return response.content[0].text.strip()


def commit_and_push(repo: Path, message: str) -> None:
    git(repo, "add", "-A")

    result = git(repo, "commit", "-m", message)
    if result.returncode != 0:
        print(f"[watchcommit] commit failed: {result.stderr.strip()}", file=sys.stderr)
        return

    result = git(repo, "push")
    if result.returncode != 0:
        print(f"[watchcommit] push failed: {result.stderr.strip()}", file=sys.stderr)
    else:
        print(f"[watchcommit] {message}")


def main() -> None:
    repo = Path(sys.argv[1]).expanduser().resolve() if len(sys.argv) > 1 else Path.home() / "dotfiles"

    if not (repo / ".git").exists():
        print(f"[watchcommit] not a git repo: {repo}", file=sys.stderr)
        sys.exit(1)

    print(f"[watchcommit] watching {repo} every {POLL_INTERVAL}s  (Ctrl-C to stop)")

    while True:
        try:
            if has_changes(repo):
                diff = build_diff(repo)
                if diff:
                    message = generate_message(diff)
                    commit_and_push(repo, message)
        except KeyboardInterrupt:
            print("\n[watchcommit] stopped")
            sys.exit(0)
        except Exception as exc:
            print(f"[watchcommit] error: {exc}", file=sys.stderr)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
