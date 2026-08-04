#!/usr/bin/env python3
"""SessionStart hook + CLI: detect (and optionally fix) drift between the
live ``~/.claude/settings.json`` / ``~/.config/opencode/opencode.jsonc`` and
their seeds in the dotfiles repo.

Why this exists
---------------
install.py's seed_file contract is copy-once: settings.json and
opencode.jsonc are written the first time install.py runs on a machine,
then never overwritten — because Claude Code and opencode both rewrite
these files in place as permissions get approved live, which would replace
a symlink with a plain file and silently detach the seed. install.py
already has helpers (``describe_settings_drift``,
``describe_opencode_drift``, ``opencode_bypass_drift``, ``json_key_drift``,
``_load_json_pair``) to report divergence — but they only run when
install.py is manually re-executed. Nothing runs them automatically, so a
live settings.json can silently drift from the repo seed for days (this
happened 2026-07-24 → 2026-07-30). This script closes that gap: a
SessionStart hook prints a one-liner per drifted file at the start of
every Claude Code session.

What counts as drift
--------------------
Only *critical* keys are surfaced:
    settings.json   → ``permissions``, ``hooks``
    opencode.jsonc  → ``permission``

Everything else (theme, model, voice, voiceEnabled, agent, tui,
statusLine, skipDangerousModePermissionPrompt, ...) is per-machine
preference and is silently ignored — the live file is allowed to differ
there. The ``opencode_bypass_drift`` check (``xargs *`` / ``awk *``
allowlist bypasses under ``permission.bash``) is surfaced verbatim as a
``SECURITY:`` line, exactly as install.py prints it.

Why import install.py
---------------------
The drift helpers are already defined and exercised there; re-implementing
would duplicate tested logic and risk divergence. install.py is
stdlib-only, imports in ~40ms, and has no top-level side effects beyond
constructing a no-op ``Palette(False)``, so it's safe to import from a
SessionStart hook.

Subcommands
-----------
    check   print a one-liner per drifted file (default; the SessionStart
            hook uses this). Silent when there's nothing to report.
    fix     surgically overwrite only the drifted critical keys in the
            live file from the seed, preserving all other live keys; backs
            up the prior live file to ``<path>.bak.YYYYmmdd-HHMMSS`` before
            writing. Never touches non-critical keys.

Usage
-----
    settings_seed_drift_check.py           # check (default)
    settings_seed_drift_check.py check
    settings_seed_drift_check.py fix

Exits 0 in all modes unless something genuinely unrecoverable happens
(unparseable args, IO error during fix) — a SessionStart hook must never
block the session.
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

# install.py lives at the repo root; import it for the drift helpers.
REPO = Path.home() / "dotfiles"
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# Module-level imports of install.py's pure drift helpers. These are pure
# functions (no install side effects); see install.py's main() guard.
import install  # noqa: E402

HOME = Path.home()
DOTFILES = REPO

# Critical keys — only these are surfaced as drift or repaired by `fix`.
# All other top-level keys are personal per-machine preferences.
SETTINGS_CRITICAL_KEYS: frozenset[str] = frozenset({"permissions", "hooks"})
OPENCODE_CRITICAL_KEYS: frozenset[str] = frozenset({"permission"})

PROFILE_MARKER = HOME / ".local" / "state" / "dotfiles" / "profile"


def resolve_profile() -> str:
    """Return "work" if this machine is work-provisioned, else "personal"."""
    try:
        return "work" if PROFILE_MARKER.read_text().strip() == "work" else "personal"
    except OSError:
        return "personal"


def settings_seed_path() -> Path:
    """Return the seed settings.json path for this machine's profile."""
    name = "settings.work.json" if resolve_profile() == "work" else "settings.json"
    return DOTFILES / "claude" / name


def opencode_seed_path() -> Path | None:
    """Return the opencode.jsonc seed path, or None on a work machine.

    opencode is never installed on a work machine (install.py rejects
    --profile=work --harness=opencode outright), so there's no seed to
    compare against there.
    """
    if resolve_profile() == "work":
        return None
    return DOTFILES / "opencode" / "opencode.jsonc"


def load_json_or_none(path: Path) -> dict[str, object] | None:
    """Load a JSON object from path, or None if it can't be read/parsed."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def critical_key_drift(
    seed: dict[str, object], live: dict[str, object], critical: frozenset[str]
) -> list[str]:
    """Return the critical keys whose values differ between seed and live.

    A key in ``critical`` that's present in one but not the other, or
    present in both with different values, counts as drifted.
    """
    return sorted(k for k in critical if seed.get(k) != live.get(k))


def settings_critical_drift(seed: Path, live: Path) -> list[str]:
    """Return the critical settings.json keys that diverged, or [] if either
    file can't be loaded."""
    pair = install._load_json_pair(seed, live)
    if pair is None:
        return []
    return critical_key_drift(*pair, SETTINGS_CRITICAL_KEYS)


def opencode_critical_drift(seed: Path, live: Path) -> str:
    """Return a drift description for opencode.jsonc critical keys, or "".

    A SECURITY bypass (xargs/awk) outranks a plain permission-key diff and
    is returned verbatim with the ``SECURITY:`` prefix, matching
    install.describe_opencode_drift.
    """
    pair = install._load_json_pair(seed, live)
    if pair is None:
        return ""
    bypasses = install.opencode_bypass_drift(*pair)
    if bypasses:
        return (
            f"SECURITY: {', '.join(bypasses)} still allowed in your live "
            "opencode.jsonc (allowlist bypass) — run "
            "`python3 ~/.claude/scripts/settings_seed_drift_check.py fix` to repair"
        )
    drifted = critical_key_drift(*pair, OPENCODE_CRITICAL_KEYS)
    return ", ".join(drifted)


def cmd_check() -> int:
    """Print a one-liner per drifted file; silent when nothing drifted."""
    settings_live = HOME / ".claude" / "settings.json"
    settings_seed = settings_seed_path()

    drifted_keys = settings_critical_drift(settings_seed, settings_live)
    if drifted_keys:
        print(
            f"settings.json drifted from seed ({settings_seed.name}) on: "
            f"{', '.join(drifted_keys)} — "
            "run `python3 ~/.claude/scripts/settings_seed_drift_check.py fix`"
        )

    oc_seed = opencode_seed_path()
    if oc_seed is not None:
        oc_live = HOME / ".config" / "opencode" / "opencode.jsonc"
        oc_drift = opencode_critical_drift(oc_seed, oc_live)
        if oc_drift:
            print(oc_drift)

    return 0


def _fix_file(
    live: Path, seed: Path, critical: frozenset[str]
) -> None:
    """Overwrite only the critical keys in `live` from `seed`, in place.

    Backs up the prior live file to <live>.bak.YYYYmmdd-HHMMSS first. Never
    touches non-critical keys.
    """
    live_data = load_json_or_none(live)
    seed_data = load_json_or_none(seed)
    if live_data is None or seed_data is None:
        print(
            f"settings_seed_drift_check: cannot fix {live} — could not parse "
            f"live or seed. Aborting this file (no changes made).",
            file=sys.stderr,
        )
        return

    changed_keys: list[str] = []
    for key in critical:
        if live_data.get(key) != seed_data.get(key):
            # If the seed has the key, write it to live. If the seed lacks
            # it but live has it (a divergence), drop it from live so live
            # matches the seed's critical portion.
            if key in seed_data:
                live_data[key] = seed_data[key]
            else:
                live_data.pop(key, None)
            changed_keys.append(key)

    if not changed_keys:
        print(f"settings_seed_drift_check: {live.name} — no critical drift to fix.")
        return

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = live.with_suffix(live.suffix + f".bak.{stamp}")
    shutil.copy2(live, backup)
    live.write_text(
        json.dumps(live_data, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"settings_seed_drift_check: repaired {live} (critical keys: "
        f"{', '.join(changed_keys)}) — backup at {backup}"
    )


def cmd_fix() -> int:
    """Surgically overwrite drifted critical keys in live files from seeds."""
    settings_live = HOME / ".claude" / "settings.json"
    settings_seed = settings_seed_path()
    if settings_live.is_file() and settings_seed.is_file():
        _fix_file(settings_live, settings_seed, SETTINGS_CRITICAL_KEYS)

    oc_seed = opencode_seed_path()
    if oc_seed is not None and oc_seed.is_file():
        oc_live = HOME / ".config" / "opencode" / "opencode.jsonc"
        if oc_live.is_file():
            _fix_file(oc_live, oc_seed, OPENCODE_CRITICAL_KEYS)
            # opencode also needs the bypass check separate from permission-key
            # overwrite — but since `fix` replaces the entire `permission` key
            # with the seed's, any xargs/awk bypasses under permission.bash are
            # dropped as a side effect. Re-check to confirm.
            pair = install._load_json_pair(oc_seed, oc_live)
            if pair is not None:
                still_bypassed = install.opencode_bypass_drift(*pair)
                if still_bypassed:
                    print(
                        f"settings_seed_drift_check: SECURITY bypass still present "
                        f"in {oc_live} after fix: {', '.join(still_bypassed)} "
                        "— delete the file and re-seed by hand",
                        file=sys.stderr,
                    )
    return 0


def main() -> int:
    args = sys.argv[1:]
    subcommand = args[0] if args else "check"
    if subcommand == "check":
        return cmd_check()
    if subcommand == "fix":
        return cmd_fix()
    print(
        f"settings_seed_drift_check: unknown subcommand {subcommand!r}",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())