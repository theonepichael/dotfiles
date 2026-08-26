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

Active-agent detection (opt-out): in addition to the manual pause file, each
poll tick scans `/proc` for processes whose cwd is inside the watched repo AND
whose name matches the agent list (or that set the `WATCHCOMMIT_AGENT=1` env
marker). When any such proc is alive, the tick is skipped entirely — no
commit, no push, no pull — so an agent editing the repo mid-session is never
raced, even if `wc-pause` wasn't called. Holding agent PIDs are surfaced via
`wc-status` (read from the agents-active state file). The message-gen
subprocess spawned by watchcommit itself runs with cwd outside any watched
repo specifically so it never self-triggers this check.

Env overrides (v1): `WATCHCOMMIT_AGENT_NAMES=foo,bar` extends the default
agent name list. `WATCHCOMMIT_IGNORE_AGENTS=1` suppresses the agent check
entirely (pause file + implicit `.git/index.lock` still apply) — use this
when a forever-resident agent (Cursor always-on LSP) would otherwise block
all fire-and-forget checkpoints.

Activity visibility: every background pull that moves HEAD, auto-commit, and
push is recorded (timestamp + resulting SHA) in the last-activity state file,
so a session or `wc-status` can tell daemon-driven git state changes from
manual ones instead of only seeing a clean/up-to-date working tree. Read by
`wc-status` and the Claude Code SessionStart hook (watchcommit_activity.py).

Automated pause (opt-in, for scripts): `wc-guard <command>` (scripts/wc-guard)
wraps a command that deliberately leaves the repo in a temporary/broken state
on purpose (e.g. a script proving a staleness check works) so watchcommit
can't auto-commit it mid-edit. Unlike wc-pause/wc-resume, wc-guard never
touches the pause-file itself — it registers its own PID under
$STATE_DIR/guard-pids while running, and is_paused() below checks both the
pause-file and any live, identity-verified wc-guard registration.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

POLL_INTERVAL = 90
MODEL = "haiku"
DEFAULT_REPO = Path(__file__).resolve().parents[1]

STATE_DIR = (
    Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state")))
    / "watchcommit"
)
PAUSE_FILE = STATE_DIR / "paused"
AGENTS_STATE_FILE = STATE_DIR / "agents-active"
ACTIVITY_STATE_FILE = STATE_DIR / "last-activity.json"
GUARD_PID_DIR = STATE_DIR / "guard-pids"

# Active-agent detection (opt-out). Linux /proc primary; non-Linux = silent
# no-op (manual wc-pause still works there). No psutil in v1.
PROC_DIR = Path("/proc")
DEFAULT_AGENT_NAMES = frozenset(
    {"claude", "opencode", "aider", "codex", "copilot", "cursor"}
)
AGENT_ENV_MARKER_KEY = "WATCHCOMMIT_AGENT"
AGENT_ENV_MARKER_VAL = "1"
MSG_GEN_CWD = Path(tempfile.gettempdir())  # invariant: never inside a watched repo

# Linux /proc/[pid]/comm truncates the executable basename to at most
# TASK_COMM_LEN-1 = 15 visible chars + null. Names <=15 chars match exactly;
# longer agent names match if the agent name starts with the (truncated) comm.
COMM_MAX = 15
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


def _proc_start_time(pid: int) -> str | None:
    """Field 22 of /proc/<pid>/stat (process start time). Used, not just a
    bare PID, to tell a live wc-guard apart from an unrelated process that
    later reused the same PID — same identity-check spirit as
    agent_active()'s name+marker check. None on any read failure (dead/
    reaped process, non-Linux, permission error) — fail-safe like the rest
    of this module's /proc handling."""
    try:
        return Path(f"/proc/{pid}/stat").read_text().split()[21]
    except (OSError, IndexError):
        return None


def guard_active() -> bool:
    """True if any $STATE_DIR/guard-pids/<pid> entry names a currently-alive
    process whose recorded start-time matches. wc-guard writes these on
    entry and removes its own on exit (even via SIGKILL's next-poll self-
    heal — a stale entry just fails the start-time comparison here, no
    sweep or cleanup step required on this side)."""
    if not GUARD_PID_DIR.exists():
        return False
    for entry in GUARD_PID_DIR.iterdir():
        if not entry.name.isdigit():
            continue
        recorded = entry.read_text().strip()
        actual = _proc_start_time(int(entry.name))
        if actual is not None and actual == recorded:
            return True
    return False


def is_paused() -> bool:
    """True if a human paused manually (PAUSE_FILE) or a wc-guard-wrapped
    command is currently running. wc-guard never touches PAUSE_FILE itself —
    this is the only place the two signals are combined."""
    return PAUSE_FILE.exists() or guard_active()


def _resolve_agent_names() -> frozenset[str]:
    """Default list extended by WATCHCOMMIT_AGENT_NAMES=foo,bar (comma-sep).
    Recomputed each tick — the set is tiny and reads are cheap; do not
    premature-optimize with lru_cache."""
    names: set[str] = set(DEFAULT_AGENT_NAMES)
    extra = os.environ.get("WATCHCOMMIT_AGENT_NAMES", "")
    if extra:
        names.update(part.strip().lower() for part in extra.split(",") if part.strip())
    return frozenset(names)


def _path_is_inside(child: Path, parent: Path) -> bool:
    """True if `child` == `parent` or is nested under it. Both paths should
    already be .resolve()'d by the caller (we resolve cwd in agent_active)."""
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    else:
        return True


def _environ_has(environ_bytes: bytes, key: str, val: str) -> bool:
    """/proc/[pid]/environ is NUL-separated KEY=VAL pairs. Exact key+val match
    — don't substring-match, WATCHCOMMIT_AGENT=1 must not hit a process that
    sets WATCHCOMMIT_AGENT=0."""
    needle = f"{key}={val}".encode()
    return needle in environ_bytes.split(b"\x00")


class AgentProc(NamedTuple):
    pid: int
    name: str
    via: str  # "name" or "marker"


def agent_active(repo: Path) -> list[AgentProc]:
    """Processes whose cwd is inside `repo` and who look like an active LLM
    agent. Returns holding PIDs in /proc iteration order (arbitrary — caller
    only cares if the list is non-empty). Fail-safe: any /proc read error
    skips that PID and continues, never raises.

    Platform contract: Linux only. Non-Linux (no /proc) → []. Manual
    wc-pause remains the escape hatch there (see header docstring).

    WATCHCOMMIT_IGNORE_AGENTS=1 = force []. Pause-file + index.lock still apply.
    """
    if not PROC_DIR.exists():
        return []
    if os.environ.get("WATCHCOMMIT_IGNORE_AGENTS") == "1":
        return []

    agent_names = _resolve_agent_names()
    own_pid = os.getpid()
    repo_resolved = repo.resolve()
    results: list[AgentProc] = []

    for pid_str in os.listdir(PROC_DIR):
        if not pid_str.isdigit():
            continue
        pid = int(pid_str)
        if pid == own_pid:
            continue

        pid_dir = PROC_DIR / pid_str

        # cwd check — the real filter. A claude subprocess running in /tmp
        # never matches, including our own message-gen (see MSG_GEN_CWD).
        cwd_link = pid_dir / "cwd"
        try:
            cwd = cwd_link.resolve()
        except (OSError, PermissionError):
            continue  # owned by another user, or zombie — fail-safe as skip
        if not _path_is_inside(cwd, repo_resolved):
            continue

        # Identity check: name match OR env marker.
        # /proc/[pid]/comm holds the executable basename truncated to COMM_MAX.
        try:
            comm = (pid_dir / "comm").read_text().strip()
        except (OSError, PermissionError):
            comm = ""

        matched_name = ""
        if comm:
            for candidate in agent_names:
                if len(candidate) <= COMM_MAX:
                    if candidate == comm:
                        matched_name = candidate
                        break
                elif candidate.startswith(comm):
                    matched_name = candidate
                    break

        if matched_name:
            results.append(AgentProc(pid, matched_name, "name"))
            continue

        # env marker — opt-in for tools that aren't in the name list
        try:
            environ = (pid_dir / "environ").read_bytes()
        except (OSError, PermissionError):
            environ = b""
        if _environ_has(environ, AGENT_ENV_MARKER_KEY, AGENT_ENV_MARKER_VAL):
            # Prefer agent name from /proc/pid/comm if non-empty, else pid-as-str
            display = comm or f"pid:{pid}"
            results.append(AgentProc(pid, display, "marker"))

    return results


def _write_agent_state(agents: list[AgentProc]) -> None:
    """One line per holding agent; latest snapshot replaces prior file. Read
    by wc-status shell helper so the user can debug why nothing's committing
    without invoking journalctl."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(f"{a.pid}\t{a.name}\t{a.via}" for a in agents) + "\n"
    AGENTS_STATE_FILE.write_text(payload)


def _record_activity(kind: str, **fields: str) -> None:
    """Merge a timestamped event under `kind` into the activity state file,
    read by wc-status and the SessionStart hook so a session can tell
    daemon-driven git state changes (pulls, auto-commits, pushes) from
    manual ones instead of only seeing a clean/up-to-date working tree."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(ACTIVITY_STATE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        data = {}
    data[kind] = {"timestamp": datetime.now(UTC).isoformat(), **fields}
    ACTIVITY_STATE_FILE.write_text(json.dumps(data, indent=2) + "\n")


def _clear_agent_state() -> None:
    """Remove the state file when no agent is holding. Unlink (not truncate)
    so wc-status distinguishes 'skipping-tick' from 'no recent skip' by file
    absence rather than size."""
    with suppress(FileNotFoundError):
        AGENTS_STATE_FILE.unlink()


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
            "--system-prompt",
            SYSTEM_PROMPT,
            "--model",
            MODEL,
            "--output-format",
            "text",
            "--no-session-persistence",
            f"Diff:\n\n{diff}",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(MSG_GEN_CWD),  # invariant: never inside a watched repo
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


def push(repo: Path, success_message: str, trigger: str) -> None:
    """Rebase onto the remote, then push. Safe to call whenever there are
    local commits not yet on the remote, regardless of what put them there."""
    if not rebase_onto_remote(repo):
        return
    result = git(repo, "push")
    if result.returncode != 0:
        print(f"[watchcommit] push failed: {result.stderr.strip()}", file=sys.stderr)
        return
    print(f"[watchcommit] {success_message}")
    sha = git(repo, "rev-parse", "HEAD").stdout.strip()
    _record_activity("last_push", sha=sha, trigger=trigger)


def commit_and_push(repo: Path, message: str) -> None:
    git(repo, "add", "-A")

    result = git(repo, "commit", "-m", message, "-m", "Committed-by: watchcommit")
    if result.returncode != 0:
        print(f"[watchcommit] commit failed: {result.stderr.strip()}", file=sys.stderr)
        return

    sha = git(repo, "rev-parse", "HEAD").stdout.strip()
    _record_activity("last_commit", sha=sha, message=message)

    push(repo, message, trigger="commit")


def pull_ff_only(repo: Path) -> None:
    """Fast-forward pull, safe to call whenever there's nothing local to
    protect — can only fast-forward or no-op, never conflict. Records the
    move in activity state only when HEAD actually changed, so a no-op poll
    tick doesn't spam last_pull with an identical from/to sha."""
    before_sha = git(repo, "rev-parse", "HEAD").stdout.strip()
    result = git(repo, "pull", "--ff-only")
    if result.returncode != 0:
        print(
            f"[watchcommit] background sync failed: {result.stderr.strip()}",
            file=sys.stderr,
        )
        return
    after_sha = git(repo, "rev-parse", "HEAD").stdout.strip()
    if after_sha != before_sha:
        _record_activity("last_pull", from_sha=before_sha, to_sha=after_sha)


def main() -> None:
    repo = (
        Path(sys.argv[1]).expanduser().resolve() if len(sys.argv) > 1 else DEFAULT_REPO
    )

    if not (repo / ".git").exists():
        print(f"[watchcommit] not a git repo: {repo}", file=sys.stderr)
        sys.exit(1)

    print(f"[watchcommit] watching {repo} every {POLL_INTERVAL}s  (Ctrl-C to stop)")

    while True:
        try:
            if is_paused():
                # Pause guard — either the touch-file (managed by wc-pause /
                # wc-resume, or a manual `touch`/`rm`) or a live wc-guard
                # registration. Skipping the cycle here holds the daemon back
                # from racing any manual git history surgery or a script's
                # deliberate temporary breakage. Logged via stderr so
                # journalctl shows the gap alongside the regular commit lines.
                print(f"[watchcommit] paused ({PAUSE_FILE})", file=sys.stderr)
            else:
                agents = agent_active(repo)
                if agents:
                    # Opt-out agent-proc detection — a live LLM agent editing
                    # the repo mid-session. Skip the entire tick so the agent
                    # is never raced; its skips are surfacable via wc-status.
                    _write_agent_state(agents)
                    summary = ", ".join(f"{a.pid}={a.name}" for a in agents)
                    print(
                        f"[watchcommit] agent-active: {summary} — skipping tick",
                        file=sys.stderr,
                    )
                elif has_changes(repo):
                    _clear_agent_state()
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
                    _clear_agent_state()
                    push(repo, "pushed previously-stuck commit(s)", trigger="retry")
                else:
                    # Nothing local to protect, so a plain fast-forward pull is
                    # always safe here. Keeps the working copy current even on
                    # machines that only ever read this repo.
                    _clear_agent_state()
                    pull_ff_only(repo)
        except KeyboardInterrupt:
            print("\n[watchcommit] stopped")
            sys.exit(0)
        except Exception as exc:
            print(f"[watchcommit] error: {exc}", file=sys.stderr)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
