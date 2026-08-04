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
already has helpers (``describe_settings_drift``, ``describe_opencode_drift``,
``opencode_bypass_drift``, ``json_key_drift``, ``_load_json_pair``) to report
divergence — but they only run when install.py is manually re-executed.
Nothing runs them automatically, so a live settings.json can silently drift
from the repo seed for days (this happened 2026-07-24 -> 2026-07-30). This
script closes that gap: a SessionStart hook prints a one-liner per drifted
file at the start of every Claude Code session.

What counts as drift
--------------------
A *denylist* of cosmetic keys is silently allowed to differ (per-machine
preferences). Everything else is reported as drift by default, so a
future security-relevant key Claude Code ships (``autoCompact``, ``sandbox``,
the already-shipped ``skipDangerousModePermissionPrompt``) is caught on the
first session after the schema change rather than silently ignored —
forcing a human to either silence it (add to cosmetics) or investigate.

    settings.json   cosmetic: theme, model, voice, voiceEnabled, agent,
                    tui, statusLine
                    reported: everything else (permissions, hooks, and any
                    unknown future top-level key)
    opencode.jsonc  cosmetic: $schema, agent
                    reported: everything else (permission, and any unknown
                    future top-level key); an ``xargs *`` / ``awk *``
                    bypass under ``permission.bash`` is surfaced verbatim
                    as a ``SECURITY:`` line, exactly as install.py prints
                    it.

Why the helpers are vendored in-script, not imported from install.py
-------------------------------------------------------------------
A SessionStart hook is leaf infrastructure; importing a 1800-line
installer to reuse ~30 lines of pure drift helpers trades trivial
one-time duplication for permanent coupling — and an import failure
(any ``ImportError`` propagating from a future edit to install.py)
would silently no-op the whole hook, disconnecting the smoke detector
from its battery. The helpers below are vendored from install.py
(see the ``# vendored from install.py`` headers); keep them in sync if
install.py's drifts.

Subcommands
-----------
    check   print a one-liner per drifted file (default; the SessionStart
            hook uses this). Silent when there's nothing to report.
            Loud-fails (prints to stdout AND exits nonzero) when a file
            should be parseable but isn't — e.g. opencode.jsonc grew a
            ``//`` comment that ``json.loads`` can't read. Never pretends
            "no drift" on a parse failure.
    fix     additively merge drifted keys from the seed into the live
            file, preserving all live-only entries (which are almost
            certainly live approvals):

            * ``permissions.allow``: union — append seed entries not in
              live; never remove live entries.
            * ``permissions.deny`` / ``permissions.ask``: overwrite from
              seed only when live is a *weakened subset* (live has fewer
              entries than the seed — a security regression); otherwise
              preserve live.
            * ``permission.bash`` (opencode): union of seed patterns into
              live; never remove live patterns — **except** the
              ``xargs *`` / ``awk *`` allowlist bypasses, which are
              stripped from live even under additive policy.
            * ``hooks``: wholesale-overwrite from seed (hooks are not
              live-modified by approval flows, so any divergence is
              genuine drift).
            * Any key that exists in both but differs in a way that isn't
              one of the above is *flag-and-skip*: printed, not touched.

            Backs up the prior live file to
            ``<path>.bak.YYYYmmdd-HHMMSS`` first; writes atomically
            (tmp + ``os.replace`` in the same dir, defense in depth
            against torn reads). Refuses to run while any Claude Code
            session is active — Claude Code holds settings in memory
            for the session's lifetime and serializes its in-memory state
            on every approval, so a mid-session fix gets silently
            clobbered on the next approval and SessionStart never re-fires
            (the session never restarted). Close your Claude Code
            sessions, then run ``fix``.

Usage
-----
    settings_seed_drift_check.py           # check (default)
    settings_seed_drift_check.py check
    settings_seed_drift_check.py fix

Exits 0 from ``check`` when there is no drift and no parse failure;
exits nonzero from ``check`` on a parse failure (so the user notices),
but never blocks the session otherwise. ``fix`` exits 1 when it refused
to run (active sessions) or hit a parse failure, 0 otherwise.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

HOME = Path.home()
DOTFILES = HOME / "dotfiles"

# Cosmetic keys — per-machine preferences explicitly allowed to drift
# silently. Anything NOT in this set is reported as drift by default, so a
# future security-relevant key Claude Code ships is caught on the first
# session after the schema change rather than silently ignored. (The prior
# allowlist design silently missed ``skipDangerousModePermissionPrompt``,
# which is security-relevant and shipped today.)
SETTINGS_COSMETIC_KEYS: frozenset[str] = frozenset(
    {
        "theme",
        "model",
        "voice",
        "voiceEnabled",
        "agent",
        "tui",
        "statusLine",
    }
)
OPENCODE_COSMETIC_KEYS: frozenset[str] = frozenset({"$schema", "agent"})

# opencode allowlist-bypass patterns — these invoke an arbitrary other
# command as their own argument (awk via system()), so their presence
# defeats the allowlist entirely. They're stripped from live even under
# the otherwise-additive fix policy: a security regression takes priority
# over preserving live-only entries.
OPENCODE_BYPASS_PATTERNS: tuple[str, ...] = ("xargs *", "awk *")

PROFILE_MARKER = HOME / ".local" / "state" / "dotfiles" / "profile"


class DriftCheckError(Exception):
    """Raised when drift checking can't proceed (parse failure, not a
    missing file). Missing files are not errors — there is simply nothing
    to compare against."""


# ── vendored from install.py — keep in sync if it drifts ─────────────────────
# These are lifted from install.py:1173-1226 so this leaf SessionStart hook
# doesn't need to import an 1800-line installer (an import failure there
# would silently no-op the whole hook). They are pure functions with no
# install side effects.


def json_key_drift(seed: dict[str, object], live: dict[str, object]) -> list[str]:
    """Return the top-level keys whose values differ between seed and live."""
    return sorted(k for k in set(seed) | set(live) if seed.get(k) != live.get(k))


def _bash_permissions(config: dict[str, object]) -> dict[str, object]:
    """Return ``permission.bash`` from an opencode config, or ``{}``."""
    permission = config.get("permission")
    if not isinstance(permission, dict):
        return {}
    bash = permission.get("bash")
    return bash if isinstance(bash, dict) else {}


def opencode_bypass_drift(
    seed: dict[str, object], live: dict[str, object]
) -> list[str]:
    """Return allowlist-bypass bash patterns present live but not in the seed.

    ``xargs`` and ``awk`` each invoke an arbitrary other command as their
    own argument (awk via ``system()``), so their presence isn't
    "individually risky command a profile could allow" — it defeats the
    allowlist entirely. They're called out separately from the generic
    top-level key diff because they live nested under ``permission.bash``,
    where a top-level diff would only say "permission" changed without
    saying which pattern came back.
    """
    seed_bash = _bash_permissions(seed)
    live_bash = _bash_permissions(live)
    return [
        k for k in OPENCODE_BYPASS_PATTERNS if k in live_bash and k not in seed_bash
    ]


# ── end vendored block ──────────────────────────────────────────────────────


# ── JSON loading: loud-fail on parse failure, silent on missing file ────────


def _load_json_strict(path: Path) -> dict[str, object]:
    """Read and parse a JSON object from ``path``.

    Raise :class:`DriftCheckError` on a parse failure (the file exists but
    isn't valid JSON — e.g. opencode.jsonc grew a ``//`` comment that
    ``json.loads`` can't read) or on any non-missing read error. Re-raise
    :class:`FileNotFoundError` unchanged so callers can tell "file is
    missing, nothing to compare" apart from "file is present but broken".
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise DriftCheckError(f"cannot read {path}: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DriftCheckError(
            f"cannot parse {path} as JSON — {exc}. If this is opencode.jsonc "
            "and has // comments, json.loads can't read JSONC; remove the "
            "comments or install a JSONC parser."
        ) from exc
    if not isinstance(data, dict):
        raise DriftCheckError(
            f"{path} parsed to {type(data).__name__}, expected a JSON object"
        )
    return data


def _load_json_pair(
    seed: Path, live: Path
) -> tuple[dict[str, object], dict[str, object]] | None:
    """Load a (seed, live) JSON pair.

    Return ``None`` if either file is *missing* (there is simply nothing
    to compare against — the caller stays silent). Raise
    :class:`DriftCheckError` if either file is present but unparseable —
    a smoke detector that silently disconnects its own battery on a parse
    failure is the failure mode to avoid here.
    """
    try:
        seed_data = _load_json_strict(seed)
    except FileNotFoundError:
        return None
    try:
        live_data = _load_json_strict(live)
    except FileNotFoundError:
        return None
    return seed_data, live_data


# ── profile resolution, seed paths ───────────────────────────────────────────


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


# ── drift detection ──────────────────────────────────────────────────────────


def _non_cosmetic_drift(
    seed: dict[str, object], live: dict[str, object], cosmetics: frozenset[str]
) -> list[str]:
    """Return non-cosmetic top-level keys whose values differ between
    seed and live. Everything in ``cosmetics`` is silently permitted to
    drift; everything else is reported."""
    keys = (set(seed) | set(live)) - cosmetics
    return sorted(k for k in keys if seed.get(k) != live.get(k))


def settings_drift(seed: Path, live: Path) -> list[str]:
    """Return the non-cosmetic settings.json keys that diverged, or [] if
    either file is missing. Raise on a parse failure."""
    pair = _load_json_pair(seed, live)
    if pair is None:
        return []
    return _non_cosmetic_drift(*pair, SETTINGS_COSMETIC_KEYS)


def opencode_drift(seed: Path, live: Path) -> str:
    """Return a drift description for opencode.jsonc non-cosmetic keys, or "".

    A SECURITY bypass (xargs/awk) outranks a plain permission-key diff and
    is returned verbatim with the ``SECURITY:`` prefix, matching
    install.describe_opencode_drift. Raise on a parse failure.
    """
    pair = _load_json_pair(seed, live)
    if pair is None:
        return ""
    bypasses = opencode_bypass_drift(*pair)
    if bypasses:
        return (
            f"SECURITY: {', '.join(bypasses)} still allowed in your live "
            "opencode.jsonc (allowlist bypass) — run "
            "`python3 ~/.claude/scripts/settings_seed_drift_check.py fix` to repair"
        )
    drifted = _non_cosmetic_drift(*pair, OPENCODE_COSMETIC_KEYS)
    return ", ".join(drifted)


def _print_loud(msg: str) -> None:
    """Print to both stdout and stderr so the message survives both the
    SessionStart hook's ``2>/dev/null`` (which only suppresses stderr) and
    manual piping of stdout elsewhere."""
    print(msg)
    print(msg, file=sys.stderr)


def cmd_check() -> int:
    """Print a one-liner per drifted file; silent when nothing drifted.
    Loud-fail (nonzero exit) on a parse failure."""
    messages: list[str] = []
    exit_code = 0

    settings_live = HOME / ".claude" / "settings.json"
    settings_seed = settings_seed_path()
    try:
        drifted = settings_drift(settings_seed, settings_live)
    except DriftCheckError as exc:
        messages.append(
            f"[settings_seed_drift_check] cannot check settings drift: {exc}"
        )
        exit_code = 1
    else:
        if drifted:
            messages.append(
                f"settings.json drifted from seed ({settings_seed.name}) on: "
                f"{', '.join(drifted)} — "
                "run `python3 ~/.claude/scripts/settings_seed_drift_check.py fix`"
            )

    oc_seed = opencode_seed_path()
    if oc_seed is not None:
        oc_live = HOME / ".config" / "opencode" / "opencode.jsonc"
        try:
            oc_drifted = opencode_drift(oc_seed, oc_live)
        except DriftCheckError as exc:
            messages.append(
                f"[settings_seed_drift_check] cannot check opencode drift: {exc}"
            )
            exit_code = 1
        else:
            if oc_drifted:
                messages.append(oc_drifted)

    if messages:
        print("\n".join(messages))
    return exit_code


# ── fix ───────────────────────────────────────────────────────────────────────


def _merge_permissions_additive(
    seed_perm: dict[str, object], live_perm: dict[str, object]
) -> tuple[dict[str, object], list[str], list[str]]:
    """Additively merge seed permissions into live.

    Return ``(new_perm, applied, skipped)`` where ``applied`` lists the
    changes that will be written and ``skipped`` lists the differing keys
    that were intentionally left untouched (flag-and-skip).

    * ``allow``: union — append seed entries not in live; never remove.
    * ``deny`` / ``ask``: overwrite from seed only when live is a
      *weakened subset* (live has fewer entries than seed — a security
      regression); otherwise preserve live.
    * Other ``permissions`` sub-keys: append from seed if missing in live;
      flag-and-skip if present in both but differ.
    """
    new: dict[str, object] = dict(live_perm)
    applied: list[str] = []
    skipped: list[str] = []

    # allow: additive union
    seed_allow = seed_perm.get("allow", [])
    live_allow = new.get("allow", [])
    if isinstance(seed_allow, list) and isinstance(live_allow, list):
        missing = [e for e in seed_allow if e not in live_allow]
        if missing:
            new["allow"] = list(live_allow) + missing
            applied.append(
                "permissions.allow +[" + ", ".join(repr(e) for e in missing) + "]"
            )
    elif "allow" in seed_perm and seed_perm.get("allow") != live_allow:
        skipped.append("permissions.allow differs — leaving live")

    # deny, ask: wholesale overwrite only when live is a weakened subset
    for sub in ("deny", "ask"):
        seed_v = seed_perm.get(sub, [])
        live_v = new.get(sub, [])
        if not isinstance(seed_v, list) or not isinstance(live_v, list):
            if sub in seed_perm and seed_perm.get(sub) != live_v:
                skipped.append(f"permissions.{sub} differs — leaving live")
            continue
        if set(live_v) < set(seed_v):
            # live is a strict subset of seed → live has fewer entries →
            # weakenend (e.g. live deny=[] but seed deny=["Bash(rm *)"]).
            new[sub] = list(seed_v)
            applied.append(
                f"permissions.{sub} restored from seed (live was weakened subset)"
            )
        elif set(live_v) != set(seed_v):
            skipped.append(
                f"permissions.{sub} differs (live={live_v!r}, seed={seed_v!r}) — leaving live"
            )

    # other permission sub-keys
    for key, value in seed_perm.items():
        if key in ("allow", "deny", "ask"):
            continue
        if key not in new:
            new[key] = value
            applied.append(f"permissions.{key} added from seed")
        elif new.get(key) != value:
            skipped.append(f"permissions.{key} differs — leaving live")

    return new, applied, skipped


def _hooks_fix(
    seed: dict[str, object], live: dict[str, object]
) -> tuple[object | None, str]:
    """Decide the wholesale-overwrite action for ``hooks``.

    Return ``(new_value, description)`` where ``new_value`` is ``None`` if
    no change is needed. A ``new_value`` of ``{}`` means "drop live's
    hooks" (seed has no hooks key or an empty hooks dict); any other dict
    means "overwrite live's hooks with this from seed".

    hooks are wholesale (not additive) because they are not live-modified
    by approval flows — any divergence is genuine drift.
    """
    seed_has_hooks = "hooks" in seed
    seed_hooks = seed.get("hooks")
    if not seed_has_hooks:
        if "hooks" in live:
            return {}, "hooks dropped (seed has none) — wholesale overwrite"
        return None, ""
    if live.get("hooks") != seed_hooks:
        return seed_hooks, "hooks overwritten from seed"
    return None, ""


def _merge_opencode_permission(
    seed_perm: dict[str, object], live_perm: dict[str, object]
) -> tuple[dict[str, object], list[str], list[str]]:
    """Additively merge seed ``permission`` into live for opencode.jsonc.

    The args are the ``permission`` sub-dicts (NOT whole top-level
    configs), so ``bash`` is read directly rather than via
    :func:`_bash_permissions` (which expects a top-level config and looks
    up ``config["permission"]["bash"]``).

    * ``permission.bash``: union — append seed patterns not in live;
      differing verdicts on shared patterns are flag-and-skip; bypass
      patterns (``xargs *`` / ``awk *``) present live-but-not-seed are
      stripped (security regression takes priority over preserving
      live-only entries).
    * ``permission.external_directory``: union of path keys (append seed
      paths not in live; differing verdicts flag-and-skip).
    * Other ``permission`` sub-keys: append from seed if missing;
      flag-and-skip if both but differ.
    """
    new: dict[str, object] = dict(live_perm)
    applied: list[str] = []
    skipped: list[str] = []

    # bash patterns: additive union + bypass removal + flag-and-skip differing
    seed_bash = seed_perm.get("bash", {})
    live_bash = new.get("bash", {})
    if isinstance(seed_bash, dict) or isinstance(live_bash, dict):
        seed_bash_d = seed_bash if isinstance(seed_bash, dict) else {}
        live_bash_d = live_bash if isinstance(live_bash, dict) else {}
        merged_bash: dict[str, object] = dict(live_bash_d)
        # append seed patterns not in live
        for pat, verdict in seed_bash_d.items():
            if pat not in merged_bash:
                merged_bash[pat] = verdict
                applied.append(f"permission.bash +[{pat!r}]")
            elif merged_bash.get(pat) != verdict:
                skipped.append(
                    f"permission.bash[{pat!r}] verdict differs "
                    f"(live={merged_bash.get(pat)!r}, seed={verdict!r}) — leaving live"
                )
        # strip bypass patterns present live-but-not-seed (security regression)
        stripped = [
            pat
            for pat in OPENCODE_BYPASS_PATTERNS
            if pat in merged_bash and pat not in seed_bash_d
        ]
        for pat in stripped:
            del merged_bash[pat]
            applied.append(f"permission.bash -[{pat!r}] (allowlist bypass removed)")
        # write back if changed (only when live actually had a bash dict or
        # the merge produced one)
        if merged_bash != live_bash_d or not isinstance(live_bash, dict):
            new["bash"] = merged_bash

    # external_directory: additive union of path keys
    for sub in ("external_directory",):
        seed_sub = seed_perm.get(sub)
        live_sub = new.get(sub)
        if isinstance(seed_sub, dict) and isinstance(live_sub, dict):
            merged: dict[str, object] = dict(live_sub)
            for key, verdict in seed_sub.items():
                if key not in merged:
                    merged[key] = verdict
                    applied.append(f"permission.{sub} +[{key!r}]")
                elif merged.get(key) != verdict:
                    skipped.append(
                        f"permission.{sub}[{key!r}] verdict differs "
                        f"(live={merged.get(key)!r}, seed={verdict!r}) — leaving live"
                    )
            if merged != live_sub:
                new[sub] = merged
        elif isinstance(seed_sub, dict) and sub not in new:
            merged = dict(seed_sub)
            new[sub] = merged
            applied.append(f"permission.{sub} added from seed")

    # other permission sub-keys
    for key, value in seed_perm.items():
        if key in ("bash", "external_directory"):
            continue
        if key not in new:
            new[key] = value
            applied.append(f"permission.{key} added from seed")
        elif new.get(key) != value:
            skipped.append(f"permission.{key} differs — leaving live")

    return new, applied, skipped


def _atomic_write(path: Path, data: dict[str, object]) -> None:
    """Write ``data`` as JSON to ``path`` atomically (tmp in the same dir +
    ``os.replace``). Defense in depth against torn reads — does NOT solve
    the semantic race with a live Claude Code session (see cmd_fix's
    refusal gate)."""
    text = json.dumps(data, indent=2) + "\n"
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmppath = tempfile.mkstemp(prefix=path.name + ".", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmppath, path)
    except BaseException:
        try:
            os.unlink(tmppath)
        except OSError:
            pass
        raise


def _fix_settings_file(live_path: Path, seed_path: Path) -> int:
    """Additively fix settings.json drift. Return 0 on success or
    no-action, 1 on parse failure."""
    try:
        live = _load_json_strict(live_path)
    except FileNotFoundError:
        return 0  # no live file → nothing to fix
    except DriftCheckError as exc:
        _print_loud(f"[settings_seed_drift_check] cannot fix {live_path}: {exc}")
        return 1
    try:
        seed = _load_json_strict(seed_path)
    except FileNotFoundError:
        return 0  # no seed → can't fix
    except DriftCheckError as exc:
        _print_loud(
            f"[settings_seed_drift_check] cannot fix — seed {seed_path} unparseable: {exc}"
        )
        return 1

    applied: list[str] = []
    skipped: list[str] = []

    # permissions: additive
    if "permissions" in seed and isinstance(seed["permissions"], dict):
        live_perm = live.get("permissions", {})
        if not isinstance(live_perm, dict):
            skipped.append("permissions: live value not an object — skipping")
        else:
            new_perm, ap, sk = _merge_permissions_additive(
                seed["permissions"], live_perm
            )
            applied.extend(ap)
            skipped.extend(sk)
            if new_perm != live_perm:
                live["permissions"] = new_perm

    # hooks: wholesale
    new_hooks, hook_desc = _hooks_fix(seed, live)
    if new_hooks is not None:
        if new_hooks == {}:
            live.pop("hooks", None)
        else:
            live["hooks"] = new_hooks  # type: ignore[assignment]
        applied.append(hook_desc)

    # Other non-cosmetic top-level keys are NOT auto-fixed — only
    # permissions and hooks are critical-and-repairable. Anything else
    # (an unknown future security key) is reported by `check` but left
    # alone by `fix`; a human decides whether to add it to the seed or
    # silence it in cosmetics.

    if not applied:
        print(
            f"settings_seed_drift_check: {live_path.name} — no auto-repairable drift."
        )
        for s in skipped:
            print(f"  skipped: {s}")
        return 0

    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    backup = live_path.with_suffix(live_path.suffix + f".bak.{stamp}")
    shutil.copy2(live_path, backup)
    _atomic_write(live_path, live)
    print(
        f"settings_seed_drift_check: repaired {live_path} "
        f"(applied: {', '.join(applied)}) — backup at {backup}"
    )
    for s in skipped:
        print(f"  skipped: {s}")
    return 0


def _fix_opencode_file(live_path: Path, seed_path: Path) -> int:
    """Additively fix opencode.jsonc drift. Return 0 on success or
    no-action, 1 on parse failure."""
    try:
        live = _load_json_strict(live_path)
    except FileNotFoundError:
        return 0
    except DriftCheckError as exc:
        _print_loud(f"[settings_seed_drift_check] cannot fix {live_path}: {exc}")
        return 1
    try:
        seed = _load_json_strict(seed_path)
    except FileNotFoundError:
        return 0
    except DriftCheckError as exc:
        _print_loud(
            f"[settings_seed_drift_check] cannot fix — seed {seed_path} unparseable: {exc}"
        )
        return 1

    applied: list[str] = []
    skipped: list[str] = []

    if "permission" in seed and isinstance(seed["permission"], dict):
        live_perm = live.get("permission", {})
        if not isinstance(live_perm, dict):
            skipped.append("permission: live value not an object — skipping")
        else:
            new_perm, ap, sk = _merge_opencode_permission(seed["permission"], live_perm)
            applied.extend(ap)
            skipped.extend(sk)
            if new_perm != live_perm:
                live["permission"] = new_perm

    if not applied:
        print(
            f"settings_seed_drift_check: {live_path.name} — no auto-repairable drift."
        )
        for s in skipped:
            print(f"  skipped: {s}")
        return 0

    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    backup = live_path.with_suffix(live_path.suffix + f".bak.{stamp}")
    shutil.copy2(live_path, backup)
    _atomic_write(live_path, live)
    print(
        f"settings_seed_drift_check: repaired {live_path} "
        f"(applied: {', '.join(applied)}) — backup at {backup}"
    )
    for s in skipped:
        print(f"  skipped: {s}")
    return 0


# ── active-session detection ─────────────────────────────────────────────────


def _roster_sessions_active() -> int:
    """Return count of active Claude Code sessions reported by the daemon
    roster (``~/.claude/daemon/roster.json``), or 0 if the roster is
    absent or reports no live supervisor / empty workers."""
    roster_path = HOME / ".claude" / "daemon" / "roster.json"
    try:
        data = json.loads(roster_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    if not isinstance(data, dict):
        return 0
    supervisor_pid = data.get("supervisorPid")
    workers = data.get("workers", {})
    if not isinstance(supervisor_pid, int) or not isinstance(workers, dict):
        return 0
    if not workers:
        return 0
    # supervisor must be alive for the workers to be considered active
    if not _pid_alive(supervisor_pid):
        return 0
    return len(workers)


def _pid_alive(pid: int) -> bool:
    """Return True if ``pid`` is a live process owned by this user."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # not our process, but it does exist
        return True
    except OSError:
        return False
    return True


def _proc_scan_sessions_active() -> int:
    """Return count of running processes whose executable basename is
    ``claude``. Linux-only (scans ``/proc``); on other platforms falls
    back to ``pgrep -x claude``."""
    if os.path.exists("/proc"):
        count = 0
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            try:
                exe = os.readlink(f"/proc/{entry}/exe")
            except OSError:
                continue
            if os.path.basename(exe) == "claude":
                count += 1
        return count
    # non-Linux fallback
    try:
        result = subprocess.run(
            ["pgrep", "-x", "claude"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired, FileNotFoundError):
        return 0
    if result.returncode != 0:
        return 0
    return len([ln for ln in result.stdout.splitlines() if ln.strip()])


def _sessions_active() -> int:
    """Return the number of active Claude Code sessions detected.

    Combines two signals (either indicates active sessions): the daemon
    roster's live supervisor with non-empty workers, and a /proc scan for
    processes whose executable basename is ``claude``. Max of the two —
    one signal catching what the other misses.
    """
    return max(_roster_sessions_active(), _proc_scan_sessions_active())


# ── subcommand entry points ──────────────────────────────────────────────────


def cmd_fix() -> int:
    """Additively repair drifted critical keys in live files from seeds.
    Refuse to run while any Claude Code session is active — Claude Code
    holds settings in memory for the session's lifetime and serializes its
    in-memory state (not a fresh read-merge-write) on every approval, so a
    mid-session fix gets silently clobbered on the next approval and
    SessionStart never re-fires."""
    n = _sessions_active()
    if n > 0:
        _print_loud(
            f"[settings_seed_drift_check] refusing to fix: {n} Claude Code "
            "session(s) active — close them first to avoid losing the fix "
            "on next approval."
        )
        return 1

    exit_code = 0
    settings_live = HOME / ".claude" / "settings.json"
    settings_seed = settings_seed_path()
    if settings_live.is_file() and settings_seed.is_file():
        exit_code = _fix_settings_file(settings_live, settings_seed) or exit_code

    oc_seed = opencode_seed_path()
    if oc_seed is not None and oc_seed.is_file():
        oc_live = HOME / ".config" / "opencode" / "opencode.jsonc"
        if oc_live.is_file():
            exit_code = _fix_opencode_file(oc_live, oc_seed) or exit_code

    return exit_code


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
