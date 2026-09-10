#!/usr/bin/env python3
"""CLI: detect and repair drift between the (under WSL) Windows-side VS
Code ``settings.json``/``keybindings.json`` and their seeds in the
dotfiles repo.

Why this exists
---------------
VS Code is the one harness whose config dotfiles still seeds directly —
Claude Code and opencode seeding moved to agent-toolkit entirely (see
CHANGELOG.md, 2026-09-09). install.py's seed_file contract is copy-once:
seeded once, then never overwritten automatically, so a live VS Code
settings/keybindings file can silently drift from the repo seed. This
script closes that gap on demand.

What counts as drift
--------------------
No cosmetic split — every VS Code setting/keybinding is a legitimate user
edit, not a security-relevant key. Drift is a plain whole-file text
comparison (not a JSON key diff): these are legal JSONC (comments,
trailing commas), which ``json.loads`` can't always parse, so text
equality is the only signal that can't misreport a commented file as
drift-free. Only checked under WSL, when a Windows-side ``code`` CLI is
found on PATH (see ``_vscode_wsl_user_dir``).

Why _vscode_wsl_user_dir/json_key_drift are vendored in-script
----------------------------------------------------------------
install.py already has equivalent helpers; these are vendored rather than
imported so this leaf CLI doesn't depend on an 1800-line installer — an
import failure there would silently break this script's own drift check.
Keep them in sync if install.py's drift (see the ``# vendored from
install.py`` headers).

Subcommands
-----------
    check   print a one-liner per drifted file (default). Silent when
            there's nothing to report. Never loud-fails — there's no
            strict JSON loader for VS Code's JSONC files (see above); the
            message is direction-neutral — ``check`` can't know
            algorithmically which side of a whole-file diff is "correct",
            so it names both ``push-vscode`` (repo->live) and
            ``sync-to-seed`` (live->repo) rather than recommending one by
            default.
    sync-to-seed
            the live->repo direction: mirror the live Windows-side
            settings/keybindings back into the dotfiles seed. Whole-file
            text copy, not a JSON round-trip, so it can't fail on native
            JSONC and can't silently reformat the user's file. Backs up
            the prior seed file to ``<path>.bak.YYYYmmdd-HHMMSS`` first;
            writes atomically (tmp + ``os.replace`` in the same dir).
            Accepts ``--dotfiles-root PATH`` to target a worktree instead
            of the live checkout (the default, resolved from this
            script's own location) — point it at a fresh worktree per the
            dotfiles-first git policy rather than writing directly into
            the main checkout.
    push-vscode
            the repo->live direction: push the dotfiles seed's
            ``settings.json``/``keybindings.json`` out to the live Windows
            user directory. WSL-only (see ``_vscode_wsl_user_dir``);
            refuses with an explicit error off WSL rather than a silent
            no-op, since this is a single-purpose command, not a
            multi-file "check everything" command like ``sync-to-seed``
            where skipping VS Code off-WSL is normal.

            For each file with a diff, prints it and asks for confirmation
            (``--yes``/``-y`` to skip the prompt; refuses on non-interactive
            stdin without ``--yes``). Confirmations for both files are
            collected before anything is written. Only once something is
            confirmed does it check whether native Windows VS Code
            (``Code.exe``) is currently running, via ``tasklist.exe`` —
            checked as late as possible, immediately before the write, to
            keep the human-response pause during confirmation out of the
            TOCTOU window. If VS Code is detected, refuses the whole push
            (no partial writes — safe because nothing was written before
            this check). The process check is best-effort (assumes "not
            running" if ``tasklist.exe`` is missing, times out, or errors)
            and backup-protected, not a guarantee: each live file gets a
            timestamped ``.bak`` before being overwritten, same convention
            as ``sync-to-seed``. Accepts ``--dotfiles-root PATH``, same as
            ``sync-to-seed``.

Usage
-----
    settings_seed_drift_check.py           # check (default)
    settings_seed_drift_check.py check
    settings_seed_drift_check.py sync-to-seed [--dotfiles-root PATH]
    settings_seed_drift_check.py push-vscode [--dotfiles-root PATH] [--yes]

``check`` always exits 0. ``sync-to-seed`` exits 1 on a ``--dotfiles-root``
that doesn't exist, 0 otherwise. ``push-vscode`` exits 1 when it refused to
run (off WSL, VS Code detected as running, or confirmation required on
non-interactive stdin without ``--yes``), 0 otherwise (including "nothing
to push").

Flags
  --quiet, -q    suppress non-essential output
  --verbose, -v  emit extra diagnostic messages to stderr
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import suppress
from datetime import datetime
from pathlib import Path

import dotfiles_cli_common as cli_common

HOME = Path.home()
DOTFILES = Path(__file__).resolve().parents[2]


# ── vendored from install.py — keep in sync if it drifts ─────────────────────
# These are lifted from install.py:1173-1226 so this leaf SessionStart hook
# doesn't need to import an 1800-line installer (an import failure there
# would silently no-op the whole hook). They are pure functions with no
# install side effects.


def json_key_drift(seed: dict[str, object], live: dict[str, object]) -> list[str]:
    """Return the top-level keys whose values differ between seed and live."""
    return sorted(k for k in set(seed) | set(live) if seed.get(k) != live.get(k))


def _vscode_wsl_user_dir() -> Path | None:
    """Locate the Windows-side VS Code user directory from WSL.

    Under WSL, VS Code is normally driven from the Windows GUI via the
    Remote-WSL extension, so the real user settings.json lives in the
    Windows user profile, not the WSL filesystem. The profile directory is
    derived from the Windows-side ``code`` shim's own path (inherited onto
    PATH via WSL interop) rather than hardcoding a username.

    Returns:
        The ``.../AppData/Roaming/Code/User`` directory, or None if no
        Windows-side ``code`` CLI is on PATH.
    """
    code_bin = shutil.which("code")
    if not code_bin:
        return None
    parts = Path(code_bin).parts
    if "AppData" not in parts or not code_bin.startswith("/mnt/"):
        return None
    win_user_dir = Path(*parts[: parts.index("AppData")])
    if "Users" not in parts:
        return None
    return win_user_dir / "AppData" / "Roaming" / "Code" / "User"


# ── end vendored block ──────────────────────────────────────────────────────


def vscode_seed_path(name: str, root: Path | None = None) -> Path:
    """Return the seed path for a VS Code file (``settings.json`` or
    ``keybindings.json``) under ``root``.

    No work/personal split like ``settings_seed_path`` — VS Code settings
    aren't profile-specific the way Claude Code's are.
    """
    return (root if root is not None else DOTFILES) / "vscode" / name


# ── drift detection ──────────────────────────────────────────────────────────


def _try_parse_json(path: Path) -> object | None:
    """Best-effort JSON parse of ``path``. Returns None on any read or
    parse failure — never raises. Used only to enrich a drift message,
    never to decide whether drift exists, so a commented (JSONC) VS Code
    file doesn't loud-fail the way ``_load_json_strict`` would."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def vscode_drift(seed: Path, live: Path) -> str:
    """Describe how a live VS Code settings.json/keybindings.json diverged
    from its seed, or "" if there's nothing to compare or nothing drifted.

    Text equality is the definitive drift signal, not JSON equality — same
    rationale as install.describe_vscode_drift: VS Code's live files are
    legal JSONC (comments, trailing commas) that ``json.loads`` can't
    parse, so a JSON-first check would miss real drift on a merely
    commented file. ``_try_parse_json`` only runs after text drift is
    already confirmed, purely to enrich the message. Never raises — there
    is no loud-fail path for VS Code drift (see module docstring).
    """
    if not seed.is_file() or not live.is_file():
        return ""
    seed_text = seed.read_text(encoding="utf-8")
    live_text = live.read_text(encoding="utf-8")
    if seed_text == live_text:
        return ""

    seed_data = _try_parse_json(seed)
    live_data = _try_parse_json(live)
    if isinstance(seed_data, dict) and isinstance(live_data, dict):
        return ", ".join(json_key_drift(seed_data, live_data))
    if isinstance(seed_data, list) and isinstance(live_data, list):
        if len(seed_data) != len(live_data):
            return f"{len(live_data)} bindings live vs {len(seed_data)} in seed"
        return f"binding definitions differ ({len(live_data)} bindings)"
    return "content differs from the repo copy"


def _print_loud(msg: str) -> None:
    """Print to both stdout and stderr so the message survives both the
    SessionStart hook's ``2>/dev/null`` (which only suppresses stderr) and
    manual piping of stdout elsewhere."""
    print(msg)
    print(msg, file=sys.stderr)


def cmd_check(quiet: bool = False) -> int:
    """Print a one-liner per drifted VS Code file; silent when nothing
    drifted. There is no loud-fail path — see ``vscode_drift``."""
    messages: list[str] = []

    vscode_user_dir = _vscode_wsl_user_dir()
    if vscode_user_dir is not None:
        for name in ("settings.json", "keybindings.json"):
            seed = vscode_seed_path(name)
            live = vscode_user_dir / name
            drift = vscode_drift(seed, live)
            if drift:
                messages.append(
                    f"{name} (VS Code) drifted from seed ({seed.name}): {drift} — "
                    "run `python3 ~/.claude/scripts/settings_seed_drift_check.py "
                    "push-vscode` to push the repo's version to Windows, or "
                    "`sync-to-seed` to pull the Windows version into the repo, "
                    "depending on which side has the change you want to keep"
                )

    if messages:
        cli_common.qprint("\n".join(messages), quiet=quiet)
    return 0


def _atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` verbatim to ``path`` atomically (tmp in the same dir
    + ``os.replace``). Used for VS Code's settings/keybindings files, which
    must be copied byte-for-byte — a JSON parse-and-reserialize round-trip
    would break on native JSONC (comments, trailing commas) and silently
    reformat the user's file even when it didn't break."""
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmppath = tempfile.mkstemp(prefix=path.name + ".", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmppath, path)
    except BaseException:
        with suppress(OSError):
            os.unlink(tmppath)
        raise


# ── sync-to-seed: reverse direction (live -> seed) ─────────────────────────


def _sync_vscode_to_seed(live_path: Path, seed_path: Path, quiet: bool = False) -> int:
    """Mirror a live VS Code settings.json/keybindings.json file back into
    its seed. Return 0 always — there's no active-session guard needed
    (this only writes the seed) and no parse-failure path to fail on,
    since this never parses JSON.

    Whole-file copy, raw text not a JSON round-trip (see
    ``_atomic_write_text``): VS Code's live files are legal JSONC
    (comments, trailing commas) that a parse-and-reserialize round-trip
    would break on, or silently reformat even when it didn't break.
    """
    if not live_path.is_file():
        return 0  # nothing live to sync
    live_text = live_path.read_text(encoding="utf-8")
    try:
        seed_text: str | None = seed_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        seed_text = None
    if seed_text == live_text:
        return 0

    backup_msg = ""
    if seed_path.is_file():
        stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
        backup = seed_path.with_suffix(seed_path.suffix + f".bak.{stamp}")
        shutil.copy2(seed_path, backup)
        backup_msg = f" — backup at {backup}"
    _atomic_write_text(seed_path, live_text)
    cli_common.qprint(
        f"settings_seed_drift_check: synced {seed_path} (from {live_path}){backup_msg}",
        quiet=quiet,
    )
    return 0


# ── push-vscode: repo -> live (guarded) ──────────────────────────────────────


def _vscode_process_running() -> bool | None:
    """Best-effort check whether native Windows VS Code (``Code.exe``) is
    currently running, via ``tasklist.exe`` from WSL.

    Matches the image name directly with a word-boundary regex, not a
    substring of the "no tasks found" message — that informational string
    is localized per-language on Windows, so substring matching on it
    would be unreliable across locales.

    Returns ``True`` if confirmed running, ``False`` if confirmed not
    running, or ``None`` if ``tasklist.exe`` isn't found, the call times
    out, or it errors — undetermined is distinct from confirmed-not-running
    so callers can decide how to treat "couldn't check" for themselves.
    """
    try:
        result = subprocess.run(
            ["tasklist.exe", "/FI", "IMAGENAME eq Code.exe"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return bool(re.search(r"\bCode\.exe\b", result.stdout, re.IGNORECASE))


def _push_vscode_to_live(seed_path: Path, live_path: Path) -> tuple[bool, str] | None:
    """Pure diff-and-plan logic for pushing one VS Code file from the repo
    seed to the live Windows path. No process check, no prompt, no write —
    those live in :func:`cmd_push_vscode`, so the same confirm-then-check-
    then-write sequence covers both files exactly once.

    ``seed_path`` must exist (it's the dotfiles repo's own seed). A missing
    ``live_path`` is treated as empty content — meaning this push will
    create the file.

    Return ``None`` if ``seed_path`` and ``live_path`` already have
    identical content (nothing to do for this file). Python's
    ``Path.read_text()`` universal-newline translation already normalizes a
    Windows-saved CRLF to LF on read, so this equality check isn't
    dominated by line-ending noise. Otherwise return ``(live_exists,
    diff)``: ``live_exists`` tells the caller whether a ``.bak`` backup
    applies; ``diff`` is a unified diff from live to seed.
    """
    seed_text = seed_path.read_text(encoding="utf-8")
    live_exists = live_path.is_file()
    live_text = live_path.read_text(encoding="utf-8") if live_exists else ""
    if seed_text == live_text:
        return None
    diff = "".join(
        difflib.unified_diff(
            live_text.splitlines(keepends=True),
            seed_text.splitlines(keepends=True),
            fromfile=str(live_path),
            tofile=str(seed_path),
        )
    )
    return live_exists, diff


# ── subcommand entry points ──────────────────────────────────────────────────


def cmd_sync_to_seed(dotfiles_root: Path, quiet: bool = False) -> int:
    """Mirror live VS Code settings back into the dotfiles seed."""
    if not dotfiles_root.is_dir():
        _print_loud(
            f"[settings_seed_drift_check] --dotfiles-root {dotfiles_root} "
            "does not exist or is not a directory."
        )
        return 1

    exit_code = 0
    vscode_user_dir = _vscode_wsl_user_dir()
    if vscode_user_dir is not None:
        for name in ("settings.json", "keybindings.json"):
            vscode_live = vscode_user_dir / name
            vscode_seed = vscode_seed_path(name, dotfiles_root)
            exit_code = (
                _sync_vscode_to_seed(vscode_live, vscode_seed, quiet=quiet) or exit_code
            )

    return exit_code


def _diff_summary(diff: str) -> str:
    """Return a one-line "N lines added, M removed" summary of a unified
    diff string, for orienting a large diff. Not required for correctness
    — the diff itself, printed alongside this, is the actual record."""
    added = sum(
        1
        for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    removed = sum(
        1
        for line in diff.splitlines()
        if line.startswith("-") and not line.startswith("---")
    )
    return f"{added} lines added, {removed} removed"


def cmd_push_vscode(dotfiles_root: Path, quiet: bool = False, yes: bool = False) -> int:
    """Push the dotfiles seed's VS Code settings.json/keybindings.json out
    to the live Windows user directory (the repo->live direction; see the
    module docstring's ``push-vscode`` section for the full guard
    rationale).

    Confirms each file's diff before writing, then checks for a running
    native Windows VS Code process immediately before writing — deliberately
    last, not first, so the (unbounded) human-response pause during
    confirmation isn't inside the TOCTOU window. No partial writes: nothing
    is written until every file needing confirmation has been confirmed
    and the process check has passed.
    """
    vscode_user_dir = _vscode_wsl_user_dir()
    if vscode_user_dir is None:
        _print_loud(
            "[settings_seed_drift_check] push-vscode only applies under WSL "
            "with a Windows-side `code` CLI on PATH."
        )
        return 1

    to_write: list[tuple[Path, Path, bool]] = []  # (seed, live, live_exists)
    for name in ("settings.json", "keybindings.json"):
        seed = vscode_seed_path(name, dotfiles_root)
        live = vscode_user_dir / name
        result = _push_vscode_to_live(seed, live)
        if result is None:
            continue
        live_exists, diff = result
        cli_common.qprint(f"{name}: {_diff_summary(diff)}", quiet=quiet)
        print(diff)  # always printed regardless of --quiet — the record of
        # what a destructive command is about to overwrite isn't chatter.

        if yes:
            confirmed = True
        elif not sys.stdin.isatty():
            _print_loud(
                "[settings_seed_drift_check] refusing: confirmation required, "
                "stdin is not interactive — pass --yes"
            )
            return 1
        else:
            answer = input(f"Push {name} to the live Windows VS Code config? [y/N] ")
            confirmed = answer.strip().lower() == "y"

        if confirmed:
            to_write.append((seed, live, live_exists))

    if not to_write:
        cli_common.qprint("settings_seed_drift_check: nothing to push.", quiet=quiet)
        return 0

    if _vscode_process_running() is not False:
        _print_loud(
            "[settings_seed_drift_check] refusing to push: VS Code appears to "
            "be running on Windows, or its status could not be verified — "
            "close it first, then re-run push-vscode (it could clobber this "
            "write on its next save)."
        )
        return 1

    pushed: list[str] = []
    for seed, live, live_exists in to_write:
        if live_exists:
            stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
            backup = live.with_suffix(live.suffix + f".bak.{stamp}")
            shutil.copy2(live, backup)
        _atomic_write_text(live, seed.read_text(encoding="utf-8"))
        pushed.append(live.name)

    cli_common.qprint(
        f"settings_seed_drift_check: pushed {', '.join(pushed)} to {vscode_user_dir}",
        quiet=quiet,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="settings_seed_drift_check")
    # --quiet/-v are defined once, on every leaf subcommand parser only
    # (via this shared `parents=` parser) -- never on `parser` itself. See
    # dev_status.py's build_parser() for the full rationale.
    verbosity_parent = argparse.ArgumentParser(add_help=False)
    cli_common.add_verbosity_args(verbosity_parent)
    subparsers = parser.add_subparsers(dest="subcommand")
    subparsers.add_parser("check", parents=[verbosity_parent])
    sync_parser = subparsers.add_parser("sync-to-seed", parents=[verbosity_parent])
    sync_parser.add_argument(
        "--dotfiles-root", type=Path, default=DOTFILES, dest="dotfiles_root"
    )
    push_parser = subparsers.add_parser("push-vscode", parents=[verbosity_parent])
    push_parser.add_argument(
        "--dotfiles-root", type=Path, default=DOTFILES, dest="dotfiles_root"
    )
    push_parser.add_argument("--yes", "-y", action="store_true")

    args = parser.parse_args(argv)
    quiet = getattr(args, "quiet", False)
    subcommand = args.subcommand or "check"
    if subcommand == "check":
        return cmd_check(quiet=quiet)
    if subcommand == "push-vscode":
        return cmd_push_vscode(args.dotfiles_root, quiet=quiet, yes=args.yes)
    return cmd_sync_to_seed(args.dotfiles_root, quiet=quiet)


if __name__ == "__main__":
    sys.exit(main())
