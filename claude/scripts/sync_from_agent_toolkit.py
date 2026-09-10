#!/usr/bin/env python3
"""sync_from_agent_toolkit.py — keep claude/CORE_INSTRUCTIONS.md current with agent-toolkit.

Not a harness-runtime script (no ``links.toml`` entry, never installed to a
harness config directory) — a repo-maintenance entrypoint run directly by
whoever maintains this repo, the same category as ``install.py``.

Since the 2026-09-10 zero-coupling flip (see agent-toolkit's ``MIGRATION.md``),
the upstream contract is a single file: ``claude/CORE_INSTRUCTIONS.md`` is
authored in agent-toolkit and flows downstream into this repo — the reverse
of the pre-flip direction, which had this repo as upstream. This tool
implements exactly that contract:

1. read ``agent-toolkit@HEAD:claude/CORE_INSTRUCTIONS.md`` (read-only —
   agent-toolkit is never written to; HEAD is resolved once per run),
2. apply TRANSFORM — the one registered mechanical rewrite this repo needs
   (the backticked ``agent-scripts/`` token, which names agent-toolkit's own
   script directory, becomes ``claude/scripts/``, which names this repo's).
   Nothing else is rewritten; every ``~/.claude/scripts/`` occurrence in
   command examples passes through untouched. The run counts substitutions
   and stops loudly on any count other than the designed cases — a
   repeatable tool never guesses,
3. compare with this repo's copy: identical → up to date, a true no-op
   (``--apply`` included: no state write, tree untouched); different and
   not ``--apply`` → summarize the pending change and stop;
4. with ``--apply`` on differing content: write the transformed file, write
   provenance state, and stage exactly two paths — the contract file and
   the state file. ``gen_core_instructions.py`` is deliberately not run
   here — it already runs separately and unconditionally covers
   regenerating ``global-instructions.md`` from whatever
   ``CORE_INSTRUCTIONS.md`` now contains, so there is no generator sweep
   for this tool to own.

Provenance (``claude/scripts/.sync-state.json``) is write-only bookkeeping:
it records the sha of the last agent-toolkit commit that touched the
contract file (not agent-toolkit HEAD — unrelated upstream commits never
churn this repo's state) and is never read back to gate anything. The sync
decision is made purely by comparing file content, so a stale, missing, or
corrupt state file can at worst cause one harmless state rewrite — never a
wrong copy or a skipped sync.

``--check`` (no equivalent upstream) is the drift guard: exits 1 if this
repo's working-tree copy doesn't match what a fresh pull from
agent-toolkit@HEAD would produce, so CI/pre-commit can catch a hand-edit of
the contract file before it ships silently diverged from upstream.

Flags: --apply, --check, --agent-toolkit-path <path>, --quiet/-q, --verbose/-v.
Env vars: none.
Files read: agent-toolkit's ``claude/CORE_INSTRUCTIONS.md`` at HEAD via
``git show`` (read-only); this repo's own ``claude/CORE_INSTRUCTIONS.md``
and ``claude/scripts/.sync-state.json``.
Files written (only with --apply): this repo's ``claude/CORE_INSTRUCTIONS.md``
and ``claude/scripts/.sync-state.json``.
Exit codes: 0 clean (up to date, or report/apply succeeded); 1 contract file
missing from agent-toolkit@HEAD, the transform guard tripped, the
agent-toolkit path is not a usable git checkout, or (``--check`` only) the
working tree has drifted from a fresh pull; 2 bad usage.
"""

import argparse
import difflib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import dotfiles_cli_common as cli_common

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_AGENT_TOOLKIT_PATH = Path.home() / "Workspace" / "agent-toolkit"

# The permanent post-flip contract (agent-toolkit's MIGRATION.md): the one
# file authored upstream in agent-toolkit and synced into this repo.
CONTRACT_FILE = "claude/CORE_INSTRUCTIONS.md"

# The registered mechanical transform. The backtick delimiters are the
# anchor: they match the prose token naming agent-toolkit's own script
# directory and never the ``~/.claude/scripts/`` occurrences inside command
# examples, which name the deployed symlink farm and must pass through
# untouched.
TRANSFORM_OLD = "`agent-scripts/`"
TRANSFORM_NEW = "`claude/scripts/`"


def state_path(repo_root: Path) -> Path:
    """Return the path to the committed sync-state marker."""
    return repo_root / "claude" / "scripts" / ".sync-state.json"


def load_state(repo_root: Path) -> dict[str, object] | None:
    """Load the sync-state marker, or None if absent or corrupt.

    Provenance-only: a corrupt file is indistinguishable from no history —
    never a crash, never a gate (the sync decision compares content).
    """
    path = state_path(repo_root)
    if not path.is_file():
        return None
    try:
        return dict[str, object](json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, OSError):
        return None


def write_state(repo_root: Path, *, agent_toolkit_sha: str) -> None:
    """Record provenance for a successful content sync.

    ``agent_toolkit_sha`` is the last agent-toolkit commit that touched
    ``CONTRACT_FILE`` (not agent-toolkit HEAD) — unrelated upstream commits
    must never churn this repo's state.
    """
    payload = {
        "last_synced_agent_toolkit_sha": agent_toolkit_sha,
        "synced_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    path = state_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


# ── git plumbing ─────────────────────────────────────────────────────────────


def run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a git command in ``repo``, capturing output as text."""
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )


def resolve_head(repo: Path) -> str:
    """Return the current HEAD commit of ``repo``."""
    result = run_git(repo, "rev-parse", "HEAD")
    if result.returncode != 0:
        raise RuntimeError(f"git rev-parse HEAD failed in {repo}: {result.stderr}")
    return result.stdout.strip()


def path_exists_at(repo: Path, ref: str, path: str) -> bool:
    """Return whether ``path`` exists in ``repo`` at ``ref``."""
    return run_git(repo, "cat-file", "-e", f"{ref}:{path}").returncode == 0


def read_at(repo: Path, ref: str, path: str) -> bytes:
    """Return the raw bytes of ``path`` in ``repo`` at ``ref``."""
    result = subprocess.run(
        ["git", "-C", str(repo), "show", f"{ref}:{path}"],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace")
        raise RuntimeError(f"git show {ref}:{path} failed in {repo}: {stderr}")
    return result.stdout


def last_commit_touching(repo: Path, ref: str, path: str) -> str:
    """The sha of the last commit in ``repo`` (at ``ref``) touching ``path``.

    Falls back to ``ref`` when history answers empty (e.g. a shallow
    clone) — provenance is best-effort bookkeeping, never gating.
    """
    result = run_git(repo, "log", "-1", "--format=%H", ref, "--", path)
    sha = result.stdout.strip() if result.returncode == 0 else ""
    return sha or ref


def git_add(repo_root: Path, paths: list[str]) -> None:
    """Stage the given repo-relative paths."""
    result = run_git(repo_root, "add", "--", *paths)
    if result.returncode != 0:
        raise RuntimeError(f"git add failed in {repo_root}: {result.stderr}")


# ── transform ────────────────────────────────────────────────────────────────


def apply_transform(text: str) -> tuple[str, int]:
    """Apply the registered transform; return (new text, substitution count)."""
    count = text.count(TRANSFORM_OLD)
    return text.replace(TRANSFORM_OLD, TRANSFORM_NEW), count


# ── pull: resolve upstream content + transform-guard verdict ────────────────


class PullError(Exception):
    """Raised when a fresh pull from agent-toolkit@HEAD can't be resolved."""


def pull_transformed(agent_toolkit_path: Path) -> tuple[str, int, str]:
    """Resolve agent-toolkit@HEAD's contract file, transformed.

    Returns ``(transformed_text, substitution_count, head_sha)``. Raises
    :class:`PullError` if the path isn't a usable checkout, the contract
    file is missing at HEAD, or it isn't valid UTF-8.
    """
    try:
        tip = resolve_head(agent_toolkit_path)
    except RuntimeError as exc:
        raise PullError(f"{agent_toolkit_path} is not a usable git checkout: {exc}")

    if not path_exists_at(agent_toolkit_path, tip, CONTRACT_FILE):
        raise PullError(
            f"{CONTRACT_FILE} is missing from agent-toolkit@{tip[:12]} — the "
            "upstream contract is broken; nothing written"
        )
    try:
        upstream_text = read_at(agent_toolkit_path, tip, CONTRACT_FILE).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PullError(
            f"{CONTRACT_FILE} in agent-toolkit@{tip[:12]} is not valid UTF-8: {exc}"
        )

    transformed, count = apply_transform(upstream_text)
    return transformed, count, tip


# ── cli ──────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="sync_from_agent_toolkit",
        description=(
            "keep claude/CORE_INSTRUCTIONS.md current with agent-toolkit@HEAD "
            "(the one permanent post-flip upstream relationship); see the "
            "module docstring for the full contract"
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="apply the sync (default: report/diff only)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if the working tree has drifted from a fresh pull, without writing anything",
    )
    parser.add_argument(
        "--agent-toolkit-path",
        metavar="<path>",
        default=None,
        help="path to the agent-toolkit checkout (default: ~/Workspace/agent-toolkit)",
    )
    cli_common.add_verbosity_args(parser)
    return parser


def main(
    repo_root: Path = REPO_ROOT,
    argv: list[str] | None = None,
    *,
    do_exit: bool = True,
) -> int:
    """Parse argv, run the single-contract sync, and report, apply, or check it.

    Returns the process exit code (0 clean; 1 failure; argparse raises
    ``SystemExit(2)`` itself on bad usage). With ``do_exit`` True (the CLI
    entrypoint), the code is also passed to ``sys.exit``.
    """

    def fail(message: str) -> int:
        print(f"[sync_from_agent_toolkit] ERROR: {message}", file=sys.stderr)
        if do_exit:
            raise SystemExit(1)
        return 1

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.check and args.apply:
        parser.error("--check and --apply are mutually exclusive")

    agent_toolkit_path = (
        Path(args.agent_toolkit_path).expanduser()
        if args.agent_toolkit_path
        else DEFAULT_AGENT_TOOLKIT_PATH
    )

    try:
        transformed, count, tip = pull_transformed(agent_toolkit_path)
    except PullError as exc:
        return fail(str(exc))

    toolkit_file = repo_root / CONTRACT_FILE
    toolkit_text = (
        toolkit_file.read_text(encoding="utf-8") if toolkit_file.is_file() else None
    )

    if toolkit_text == transformed:
        if not args.check:
            cli_common.qprint(
                f"[sync_from_agent_toolkit] up to date with agent-toolkit@{tip[:12]}",
                quiet=args.quiet,
            )
        if do_exit:
            raise SystemExit(0)
        return 0

    if args.check:
        cli_common.qprint(
            f"[sync_from_agent_toolkit] DRIFT: working tree does not match a "
            f"fresh pull from agent-toolkit@{tip[:12]} — run `python3 "
            "claude/scripts/sync_from_agent_toolkit.py --apply`",
            quiet=args.quiet,
        )
        if do_exit:
            raise SystemExit(1)
        return 1

    if count == 0:
        return fail(
            "the copies differ but the transform made 0 substitutions — "
            "upstream likely reworded away the backticked `agent-scripts/` "
            "token; review the upstream change and port it by hand (or "
            "adjust TRANSFORM) rather than blind-copying it"
        )
    if count > 1:
        return fail(
            f"the transform matched {count} occurrences of {TRANSFORM_OLD!r} "
            "(expected 1) — upstream prose changed in a way the transform "
            "was not designed for; review before syncing"
        )

    if not args.apply:
        diff = list(
            difflib.unified_diff(
                (toolkit_text or "").splitlines(),
                transformed.splitlines(),
                fromfile=f"dotfiles:{CONTRACT_FILE}",
                tofile=f"agent-toolkit@{tip[:12]}:{CONTRACT_FILE}",
                lineterm="",
            )
        )
        cli_common.qprint(
            f"[sync_from_agent_toolkit] BASE(dotfiles) → TIP(agent-toolkit@{tip[:12]}); "
            f"transform substitutions: {count}",
            quiet=args.quiet,
        )
        for line in diff[:40]:
            cli_common.qprint(f"  {line}", quiet=args.quiet)
        if len(diff) > 40:
            cli_common.qprint(f"  … {len(diff) - 40} more diff lines", quiet=args.quiet)
        cli_common.qprint(
            "[sync_from_agent_toolkit] report only — pass --apply to write",
            quiet=args.quiet,
        )
        if do_exit:
            raise SystemExit(0)
        return 0

    toolkit_file.write_text(transformed, encoding="utf-8")
    cli_common.qprint(
        f"[sync_from_agent_toolkit] synced {CONTRACT_FILE} from "
        f"agent-toolkit@{tip[:12]} (transform substitutions: {count})",
        quiet=args.quiet,
    )

    touching = last_commit_touching(agent_toolkit_path, tip, CONTRACT_FILE)
    if touching == tip:
        cli_common.vprint(
            "provenance: no dedicated upstream commit found for "
            f"{CONTRACT_FILE}; recording TIP {tip[:12]} as best effort",
            verbose=args.verbose,
        )
    write_state(repo_root, agent_toolkit_sha=touching)
    git_add(
        repo_root, [CONTRACT_FILE, str(state_path(repo_root).relative_to(repo_root))]
    )

    cli_common.qprint(
        f"[sync_from_agent_toolkit] applied; staged {CONTRACT_FILE} and "
        f"{state_path(repo_root).relative_to(repo_root)} (provenance: upstream "
        f"{touching[:12]}) — review the staged diff and commit it yourself; "
        "this tool does not commit on your behalf",
        quiet=args.quiet,
    )
    if do_exit:
        raise SystemExit(0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
