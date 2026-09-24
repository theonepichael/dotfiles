#!/usr/bin/env python3
"""dev_status_sync.py — cross-machine sync for dev_status.py's backlog/pending store.

Always initiated from this machine (desktop-initiated-only): a run performs a
full bidirectional merge in one command — pulls the remote's state, merges it
against a local 3-way base snapshot, pushes the merged result back to the
remote, and saves locally. ``dev_status.py`` itself is not modified; this is a
standalone bolt-on script, stdlib Python imports; requires ``rsync`` at
runtime (validated locally via ``shutil.which`` and remotely via a preflight
before any transfer).

Transport is staged export -> local merge -> import over SSH, reusing
``dev_status.py``'s own optimistic-concurrency (``--if-rev``) pattern rather
than holding a live bidirectional session. See the companion plan document
for the full design rationale (3-way merge algorithm, conflict log, graph
integrity pass, path mapping, locking).

In addition to the JSON store, ``sync`` transfers the ``~/.claude/data/grill/``
files referenced by an item's ``related_files`` (specs, grill plans, critique
notes) in both directions, guarded by an mtime check so neither side's newer
edit is clobbered. Project-source ``related_files`` entries (any path outside
that grill prefix) are never transferred.

Flags
  --quiet, -q    suppress non-essential output
  --verbose, -v  emit extra diagnostic messages to stderr
  sync --no-artifacts     skip grill/ artifact transfer (metadata-only sync)
  sync --dry-run          report only; print the would-transfer artifact set
  state                   print this machine's migration/layout state (framed JSON)

Migration safety: a sync refuses while either machine is mid-way through a
toolkit-home migration (its migration lock is held exclusively, or a
migration journal is unfinished or unreadable), or when the two machines are
on different toolkit layouts. A committed migration awaiting finalize is
allowed. Each side holds the migration lock shared for the duration of its
own work, and the desktop re-reads the remote's state just before its local
commit.

Requires Python 3.12+.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import random
import re
import secrets
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import cast

# The toolkit modules this script imports live beside it in ~/.claude/scripts
# today and in ~/.agent-toolkit/scripts once the toolkit-home migration lands.
_REQUIRED_TOOLKIT_MODULES = (
    "dev_status",
    "dev_status_mutation",
    "agent_toolkit_paths",
    "migration_lock",
)


def toolkit_scripts_dir(candidates: Sequence[Path] | None = None) -> Path | None:
    """The toolkit checkout directory to import ``dev_status`` and friends from.

    Tries each install directory in order (``~/.agent-toolkit/scripts``, then
    ``~/.claude/scripts``) and accepts the first one where every required
    module is present and all of them resolve into one directory -- a
    half-updated install is skipped rather than mixed. Returns that resolved
    directory, or None when no candidate qualifies.
    """
    if candidates is None:
        home = Path.home()
        candidates = (home / ".agent-toolkit" / "scripts", home / ".claude" / "scripts")
    for directory in candidates:
        modules = [directory / f"{name}.py" for name in _REQUIRED_TOOLKIT_MODULES]
        if not all(m.is_file() for m in modules):
            continue
        parents = {m.resolve().parent for m in modules}
        if len(parents) == 1:
            return parents.pop()
    return None


_TOOLKIT_DIR = toolkit_scripts_dir()
sys.path.insert(0, str(_TOOLKIT_DIR or Path(__file__).parent))
import agent_toolkit_paths  # noqa: E402
import dev_status  # noqa: E402
import dotfiles_cli_common as cli_common  # noqa: E402
import migration_lock  # noqa: E402

PROTOCOL_VERSION = 3

# The one directory this feature ever touches: the grill/ tree under a home
# prefix (e.g. /home/yanil/.claude/data/grill). Every artifact-collection and
# transfer rule is scoped to this prefix only — a related_files path pointing
# at ordinary project source (or anything else under home) is never collected,
# never rsynced, and never a reason to warn.
GRILL_SUBPATH = ".claude/data/grill"

# rsync's remote shell, mirroring ssh_run()'s ssh options exactly so transport
# behavior matches the JSON channel.
_RSH = "ssh -o ConnectTimeout=10 -o ServerAliveInterval=5 -o ServerAliveCountMax=2"

SYNC_BASE_FILE = dev_status.DATA_DIR / "_sync-base.json"
CONFLICT_LOG_FILE = dev_status.DATA_DIR / "_sync-conflicts.jsonl"

DEFAULT_HOST = "fedora"
DEFAULT_REMOTE_SCRIPT = "~/.claude/scripts/dev_status_sync.py"
DEFAULT_REMOTE_USER = "theon"
DEFAULT_USER_MAP = {"yanil": "/home/yanil", "theon": "/home/theon"}

_FRAME_MARK = "DEVSTATUS_SYNC_JSON"
_FRAME_RE = re.compile(
    rf"==={_FRAME_MARK}_START:([0-9a-f]+)===\n(.*?)\n==={_FRAME_MARK}_END:\1===",
    re.DOTALL,
)

_LOCK_POLL_INTERVAL = 0.2


class SyncFatalError(Exception):
    """A non-retryable sync failure. Maps to exit code 1."""


class SyncRetryableError(Exception):
    """A retryable sync condition (stale rev, lock timeout, SSH hiccup). Exit code 2."""


# ── migration safety ──────────────────────────────────────────────────────────

MIGRATION_LOCK_SITE = "dev-status-sync"
MIGRATION_TERMINAL_EVENTS = frozenset({"end", "abandoned"})
_MIGRATION_JOURNALS = ("journal.jsonl", "rollback.jsonl", "finalize.jsonl")
_UNSAFE_MIGRATION_STATES = frozenset({"in-flight", "unknown"})


class JournalCorruptError(Exception):
    """A migration journal has a malformed line before its last one."""


def _parse_journal_line(line: bytes) -> dict[str, object] | None:
    if not line.strip():
        return None
    try:
        parsed = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def read_journal(path: Path) -> list[dict[str, object]] | None:
    """Valid records of one migration journal, or None if the file is absent.

    Mirrors the migrator's own reader: a malformed final line is a torn write
    and is skipped; a malformed earlier line raises :class:`JournalCorruptError`.
    """
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    records: list[dict[str, object]] = []
    lines = raw.split(b"\n")
    complete, tail = lines[:-1], lines[-1]
    for index, line in enumerate(complete):
        record = _parse_journal_line(line)
        if record is None:
            if index == len(complete) - 1 and not tail:
                return records
            raise JournalCorruptError(f"{path}: malformed journal line {index + 1}")
        records.append(record)
    if tail:
        record = _parse_journal_line(tail)
        if record is not None:
            records.append(record)
    return records


def _journal_outcome(records: list[dict[str, object]] | None) -> str | None:
    """The ``end`` outcome of a finished journal, else None."""
    if not records or records[-1].get("event") != "end":
        return None
    detail = records[-1].get("detail")
    return str(detail.get("outcome")) if isinstance(detail, dict) else None


def installer_state_dir() -> Path:
    """Where the migrator keeps its journals (it ignores XDG_STATE_HOME)."""
    return Path.home() / ".local" / "state" / "agent-toolkit"


def migration_status(installer_state: Path) -> tuple[str, str]:
    """Classify this machine's toolkit-home migrations as ``(state, detail)``.

    ``state`` is ``"in-flight"`` when any migration's main journal is missing,
    empty or unfinished, or a rollback/finalize journal exists but is
    unfinished; ``"committed-unfinalized"`` when a committed migration awaits
    finalize (allowed: the rollout syncs on the new layout before finalizing);
    ``"unknown"`` when a journal cannot be read; otherwise ``"idle"``.
    """
    root = installer_state / "migrations"
    if not root.is_dir():
        return ("idle", "")
    awaiting: list[str] = []
    try:
        directories = sorted(
            p for p in root.iterdir() if p.is_dir() and not p.is_symlink()
        )
        for directory in directories:
            journals = {
                name: read_journal(directory / name) for name in _MIGRATION_JOURNALS
            }
            for name, records in journals.items():
                if records is None and name != "journal.jsonl":
                    continue
                if (
                    not records
                    or records[-1].get("event") not in MIGRATION_TERMINAL_EVENTS
                ):
                    return (
                        "in-flight",
                        f"migration {directory.name}: {name} is unfinished",
                    )
            if (
                _journal_outcome(journals["journal.jsonl"]) == "committed"
                and _journal_outcome(journals["finalize.jsonl"]) != "finalized"
                and _journal_outcome(journals["rollback.jsonl"]) != "rolled-back"
            ):
                awaiting.append(directory.name)
    except (JournalCorruptError, OSError) as exc:
        return ("unknown", str(exc))
    if awaiting:
        return ("committed-unfinalized", ", ".join(awaiting))
    return ("idle", "")


def migration_lock_held_exclusively() -> bool | None:
    """Whether a migration holds this machine's migration lock right now.

    A non-blocking *shared* flock on a separate descriptor: it never conflicts
    with this process's own shared scope, only with an exclusive holder.
    None when the lock file cannot be checked.
    """
    try:
        fd = os.open(migration_lock.lock_path(), os.O_RDONLY)
    except FileNotFoundError:
        return False
    except OSError:
        return None
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    except OSError:
        return None
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def _current_layout() -> str:
    try:
        return str(agent_toolkit_paths.current_layout())
    except (agent_toolkit_paths.LayoutError, OSError, ValueError):
        return "unknown"


def machine_state() -> dict[str, str]:
    """This machine's layout, migration state and resolved grill root."""
    layout = _current_layout()
    decisions_root = ""
    if layout != "unknown":
        try:
            decisions_root = os.path.abspath(agent_toolkit_paths.path_for("decisions"))
        except (agent_toolkit_paths.LayoutError, OSError, ValueError):
            layout = "unknown"
    held = migration_lock_held_exclusively()
    if held is None:
        migration, detail = "unknown", "the migration lock could not be checked"
    elif held:
        migration, detail = "in-flight", "the migration lock is held exclusively"
    else:
        migration, detail = migration_status(installer_state_dir())
    return {
        "layout": layout,
        "migration": migration,
        "decisions_root": decisions_root,
        "detail": detail,
    }


def refuse_unsafe_states(local: dict[str, str], remote: object, host: str) -> None:
    """Raise SyncFatalError unless both machines are safe to sync together."""
    if not isinstance(remote, dict):
        raise SyncFatalError(
            f"{host} sent no machine state -- redeploy dev_status_sync.py there"
        )
    for side, state in (("this machine", local), (host, remote)):
        if state.get("migration") in _UNSAFE_MIGRATION_STATES:
            raise SyncFatalError(
                f"{side} is mid-migration (state {state.get('migration')}: "
                f"{state.get('detail')}) -- refusing to sync; retry once it finishes"
            )
        if state.get("layout") in (None, "unknown"):
            raise SyncFatalError(
                f"{side} has an unknown toolkit layout -- refusing to sync"
            )
    if local.get("layout") != remote.get("layout"):
        raise SyncFatalError(
            f"layout mismatch: this machine is {local.get('layout')}, {host} is "
            f"{remote.get('layout')} -- both machines must be on the same layout"
        )


_LAYOUT_AT_IMPORT = _current_layout()


@contextmanager
def migration_guard() -> Iterator[dict[str, str]]:
    """Hold the migration lock shared and yield this machine's state under it.

    Refuses when a migration holds the lock, and when the layout has changed
    since this process started (the store paths ``dev_status`` computed at
    import would then point at the old layout).
    """
    try:
        with migration_lock.shared(MIGRATION_LOCK_SITE, quiet=True):
            state = machine_state()
            if state["layout"] != _LAYOUT_AT_IMPORT:
                raise SyncFatalError(
                    f"toolkit layout changed from {_LAYOUT_AT_IMPORT} to "
                    f"{state['layout']} since this process started -- rerun"
                )
            yield state
    except migration_lock.MigrationLockBusy as exc:
        raise SyncFatalError(
            f"a toolkit-home migration is running on this machine: {exc}"
        ) from exc


# ── local lock (can't reuse dev_status.backlog_lock — needs a timeout) ────────


@contextmanager
def local_lock(timeout: float) -> Iterator[None]:
    """Hold this machine's exclusive backlog lock, polling with a deadline.

    ``dev_status.backlog_lock()`` wraps a bare blocking ``flock`` with no
    timeout — exactly what this replaces, so every other ``dev_status.py``
    command doesn't hang forever if a sync is holding the lock. Polls with
    jitter so two colliding syncs don't retry in lockstep.
    """
    dev_status.DATA_DIR.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    with open(dev_status.LOCK_FILE, "w") as f:
        try:
            while True:
                try:
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise SyncRetryableError(
                            f"could not acquire local lock within {timeout}s — "
                            "another dev_status.py/dev_status_sync.py process is holding it"
                        )
                    time.sleep(_LOCK_POLL_INTERVAL + random.uniform(0, 0.1))
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


# ── I/O (reads schema_version as data, never routes through load_items()/load_pending()) ──


def _read_store_file(path: Path) -> tuple[list[dict[str, object]], object]:
    """Read a dev_status.py data file directly, without its hard sys.exit(1).

    Returns:
        ``(items, schema_version)``. ``([], None)`` if the file doesn't exist.

    Raises:
        SyncFatalError: If the file exists but isn't valid JSON, or isn't a
            JSON object.
    """
    if not path.exists():
        return [], None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        raise SyncFatalError(f"corrupt {path}: {e}") from e
    if not isinstance(data, dict):
        raise SyncFatalError(f"{path} is not a JSON object")
    return cast(list[dict[str, object]], data.get("items", [])), data.get(
        "schema_version"
    )


def load_sync_base(
    local_schema: dict[str, object],
) -> tuple[list[dict[str, object]] | None, list[dict[str, object]] | None]:
    """Load ``_sync-base.json``, per-store, treating a schema-stale store as absent.

    Returns:
        ``(base_items, base_pending)``, each ``None`` if unusable for that
        store (missing file, or that store's recorded schema_version doesn't
        match ``local_schema`` — a local schema upgrade, not a cross-machine
        disagreement).

    Raises:
        SyncFatalError: If the file exists but fails to parse — distinct
            from "missing or schema-stale," which fall back gracefully to a
            union merge; a corrupt file signals a bug, not a normal state
            transition.
    """
    if not SYNC_BASE_FILE.exists():
        return None, None
    try:
        data = json.loads(SYNC_BASE_FILE.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        raise SyncFatalError(f"corrupt {SYNC_BASE_FILE}: {e}") from e
    if not isinstance(data, dict):
        raise SyncFatalError(f"{SYNC_BASE_FILE} is not a JSON object")
    base_schema = cast(dict[str, object], data.get("schema_version") or {})
    items = (
        cast(list[dict[str, object]], data.get("items"))
        if base_schema.get("items") == local_schema.get("items")
        else None
    )
    pending = (
        cast(list[dict[str, object]], data.get("pending_items"))
        if base_schema.get("pending_items") == local_schema.get("pending_items")
        else None
    )
    return items, pending


def save_sync_base(
    local_schema: dict[str, object],
    items: list[dict[str, object]],
    pending: list[dict[str, object]],
) -> None:
    """Atomically persist the post-sync state as the new base snapshot."""
    payload = json.dumps(
        {"schema_version": local_schema, "items": items, "pending_items": pending},
        indent=2,
    )
    dev_status._atomic_write_json(SYNC_BASE_FILE, payload, ".syncbase_tmp_")


def _append_conflict_log(conflicts: list[dict[str, object]]) -> None:
    """Append staged conflict entries to ``_sync-conflicts.jsonl``, durably."""
    dev_status.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFLICT_LOG_FILE, "a") as f:
        f.writelines(json.dumps(c, sort_keys=True) + "\n" for c in conflicts)
        f.flush()
        os.fsync(f.fileno())


# ── path mapping (related_files across yanil/theon home dirs) ─────────────────


def _content_hash(item: dev_status.BacklogItem) -> str:
    func = getattr(dev_status, "_content_hash", None)
    if func is not None and func is not _content_hash:
        return func(item)
    import dev_status_mutation

    return dev_status_mutation._content_hash(item)


def rewrite_related_files_paths(
    item: dict[str, object],
    from_home: str,
    to_home: str,
    root_map: tuple[str, str] | None = None,
) -> dict[str, object]:
    """Rewrite a leading ``from_home`` prefix on ``related_files.path`` entries.

    Recomputes ``review_content_hash`` (via ``_content_hash``)
    whenever a rewrite actually changes ``related_files``, since that field
    feeds the hash and a stale hash would break ``approve``/``reject``.
    Narrow prefix substitution only — not a general path-portability system;
    a path already in ``to_home`` form, or with no ``/home/<user>/`` prefix
    at all (``/mnt/c/...``, ``/tmp/...``), passes through untouched.

    ``root_map`` is ``(from_grill_root, to_grill_root)``: a path under the
    source machine's grill root is re-rooted onto the destination's grill
    root first, since the two roots need not sit at the same home-relative
    path (and a root under ``/home/...`` would otherwise match the home rule).
    """
    rf = item.get("related_files")
    if not rf or not isinstance(rf, list):
        return item
    rules = [(from_home.rstrip("/"), to_home.rstrip("/"))]
    if root_map is not None:
        rules.insert(0, (root_map[0].rstrip("/"), root_map[1].rstrip("/")))
    changed = False
    new_rf: list[object] = []
    for entry in rf:
        path = entry.get("path") if isinstance(entry, dict) else None
        rewritten = None
        if isinstance(entry, dict) and isinstance(path, str):
            for src, dst in rules:
                if path.startswith(src + "/"):
                    rewritten = dst + path[len(src) :]
                    break
        if rewritten is not None and rewritten != path:
            new_entry = dict(cast(dict[str, object], entry))
            new_entry["path"] = rewritten
            new_rf.append(new_entry)
            changed = True
        else:
            new_rf.append(entry)
    if not changed:
        return item
    new_item = dict(item)
    new_item["related_files"] = new_rf
    if "review_content_hash" in new_item:
        new_item["review_content_hash"] = _content_hash(
            cast(dev_status.BacklogItem, new_item)
        )
    return new_item


def rewrite_paths_list(
    items: list[dict[str, object]],
    from_home: str,
    to_home: str,
    root_map: tuple[str, str] | None = None,
) -> list[dict[str, object]]:
    """Apply :func:`rewrite_related_files_paths` across a whole store."""
    return [
        rewrite_related_files_paths(it, from_home, to_home, root_map) for it in items
    ]


# ── artifact transfer (~/.claude/data/grill/ files referenced by related_files) ──


def grill_root_for(home: str, grill_root: str | None = None) -> str:
    """The grill directory to use: ``grill_root`` when given, else the legacy
    ``{home}/.claude/data/grill``."""
    if grill_root:
        return grill_root.rstrip("/")
    return f"{home.rstrip('/')}/{GRILL_SUBPATH}"


def _warn(msg: str) -> None:
    """Emit a non-fatal warning to stderr (never suppressed by --quiet)."""
    print(f"warn: {msg}", file=sys.stderr)


def _related_paths(item: object) -> list[str]:
    """Extract ``related_files[].path`` string values from a store item."""
    if not isinstance(item, dict):
        return []
    rf = item.get("related_files")
    if not isinstance(rf, list):
        return []
    out: list[str] = []
    for entry in rf:
        if isinstance(entry, dict):
            path = entry.get("path")
            if isinstance(path, str):
                out.append(path)
    return out


def collect_artifact_paths(
    items: list[dict[str, object]], home: str, grill_root: str | None = None
) -> list[Path]:
    """Return distinct, sorted artifact paths to transfer for ``items``.

    Only ``related_files[]`` paths under ``{home}/.claude/data/grill/`` are
    in scope — the one directory this session's own tooling writes (specs,
    grill plans, critique notes, the ``vitals/`` subtree). Everything else
    under home (project source in its own git repo, dotfiles, ``/tmp``, a
    worktree directory) is excluded here, not handled as a downstream edge
    case. Within the grill prefix, symlink entries, ``..``/symlink escapes
    out of the directory, the grill directory itself, and anything that
    isn't a regular file or directory are skipped. Escape/symlink/self
    warnings are emitted by ``warn_nonlocal_related_paths`` (not here, so
    the transfer stage doesn't double-warn).
    """
    out: list[Path] = []
    seen: set[str] = set()
    for item in items:
        for p in _related_paths(item):
            kind, _ = _grill_classify(p, home, grill_root)
            if kind == "out":
                continue  # out of scope (project source, dotfiles, etc.)
            if kind == "escape":
                continue  # warned by warn_nonlocal_related_paths
            # include
            resolved = Path(p).resolve()
            if not (resolved.is_file() or resolved.is_dir()):
                continue  # not present locally yet (pull will fetch it)
            if p in seen:
                continue
            seen.add(p)
            out.append(Path(p))
    return sorted(out, key=lambda x: str(x))


def _grill_classify(
    path_str: str, home: str, grill_root: str | None = None
) -> tuple[str, str | None]:
    """Classify a ``related_files`` path against the grill scope.

    Returns ``(kind, warning)`` where ``kind`` is one of:

    * ``"out"``     - not under ``{home}/.claude/data/grill/`` (project
      source, dotfiles, ``/tmp``, a worktree, ...). Correctly out of scope.
    * ``"escape"``  - raw grill prefix but resolves outside the grill dir,
      is a symlink, or is the grill dir itself. Excluded; ``warning`` is the
      message to emit (or ``None`` when no warning is wanted).
    * ``"include"`` - under grill and resolvable as a real file/dir. In scope
      for transfer.

    ``home`` here is the *local* home; ``merged`` is always in local form by
    the time this runs (see ``cmd_sync``), so the prefix check is form-correct.
    """
    root = grill_root_for(home, grill_root)
    if not path_str.startswith(root + "/"):
        return ("out", None)
    path = Path(path_str)
    if path.is_symlink():
        return ("escape", f"skipping symlinked related_files path: {path_str}")
    resolved = path.resolve()
    resolved_root = Path(root).resolve()
    if resolved == resolved_root:
        return ("escape", f"skipping grill-dirself related_files path: {path_str}")
    if resolved_root not in resolved.parents:
        return (
            "escape",
            f"skipping related_files path escaping grill dir: {path_str}",
        )
    return ("include", None)


def remote_has_rsync(host: str, ssh_timeout: float) -> bool:
    """Preflight: is ``rsync`` available on the remote over SSH?"""
    try:
        proc = subprocess.run(
            [
                "ssh",
                "-o",
                "ConnectTimeout=10",
                "-o",
                "ServerAliveInterval=5",
                "-o",
                "ServerAliveCountMax=2",
                host,
                "command -v rsync",
            ],
            capture_output=True,
            timeout=ssh_timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False
    return proc.returncode == 0


def _rel_for_root(path: Path, root: str) -> str:
    """Relative path of ``path`` under ``root`` (for the rsync ``/./`` anchor)."""
    return str(path.resolve().relative_to(Path(root).resolve()))


def _rsync_argv(srcs: list[str], dest: str, rsync_io_timeout: float) -> list[str]:
    """Build a single batched rsync argv (no shell — all single args)."""
    return [
        "rsync",
        "-rptDuR",
        "--mkpath",
        "-e",
        _RSH,
        "--timeout",
        str(int(rsync_io_timeout)),
        *srcs,
        dest,
    ]


def _run_rsync(argv: list[str], ssh_timeout: float) -> None:
    """Run rsync; any non-zero is fatal (we must not commit metadata pointing
    at a file that failed to arrive)."""
    try:
        proc = subprocess.run(
            argv, capture_output=True, timeout=ssh_timeout, check=False
        )
    except subprocess.TimeoutExpired as e:
        raise SyncFatalError(
            f"artifact rsync timed out after {ssh_timeout}s: {e}"
        ) from e
    if proc.returncode != 0:
        stderr_text = proc.stderr.decode("utf-8", errors="replace")
        raise SyncFatalError(
            f"artifact rsync failed (exit {proc.returncode}): {stderr_text}"
        )


def push_artifacts(
    host: str,
    items: list[dict[str, object]],
    local_home: str,
    remote_home: str,
    ssh_timeout: float,
    rsync_io_timeout: float,
    *,
    quiet: bool,
    dry_run: bool,
    local_root: str | None = None,
    remote_root: str | None = None,
) -> tuple[int, int]:
    """Push local ``grill/`` artifacts to ``host``. Returns (attempted, failed).

    Pre-filters missing local sources (warned, not attempted — the
    user-requested graceful case). Empty set short-circuits with ``(0, 0)``
    and no rsync call. A non-zero rsync is fatal.
    """
    lroot = grill_root_for(local_home, local_root)
    rroot = grill_root_for(remote_home, remote_root)
    paths = collect_artifact_paths(items, local_home, lroot)
    existing: list[Path] = []
    for p in paths:
        if p.exists():
            existing.append(p)
        else:
            _warn(f"source missing locally: {p}")
    attempted = len(existing)
    if attempted == 0:
        return (0, 0)
    rels = sorted({_rel_for_root(p, lroot) for p in existing})
    srcs = [f"{lroot}/./{rel}" for rel in rels]
    dest = f"{host}:{rroot}/"
    argv = _rsync_argv(srcs, dest, rsync_io_timeout)
    if dry_run:
        cli_common.qprint("would rsync (push):", quiet=quiet)
        cli_common.qprint("  " + " ".join(argv), quiet=quiet)
        return (attempted, 0)
    _run_rsync(argv, ssh_timeout)
    return (attempted, 0)


def pull_artifacts(
    host: str,
    items: list[dict[str, object]],
    local_home: str,
    remote_home: str,
    ssh_timeout: float,
    rsync_io_timeout: float,
    *,
    quiet: bool,
    dry_run: bool,
    local_root: str | None = None,
    remote_root: str | None = None,
) -> tuple[int, int]:
    """Pull ``grill/`` artifacts from ``host`` to local. Returns (attempted, failed).

    Mirrors :func:`push_artifacts` but the source is the remote. No local
    existence pre-filter — a missing remote source surfaces as a non-zero
    rsync and correctly aborts rather than committing a broken reference.
    """
    lroot = grill_root_for(local_home, local_root)
    rroot = grill_root_for(remote_home, remote_root)
    paths = collect_artifact_paths(items, local_home, lroot)
    attempted = len(paths)
    if attempted == 0:
        return (0, 0)
    rels = sorted({_rel_for_root(p, lroot) for p in paths})
    srcs = [f"{host}:{rroot}/./{rel}" for rel in rels]
    dest = f"{lroot}/"
    argv = _rsync_argv(srcs, dest, rsync_io_timeout)
    if dry_run:
        cli_common.qprint("would rsync (pull):", quiet=quiet)
        cli_common.qprint("  " + " ".join(argv), quiet=quiet)
        return (attempted, 0)
    _run_rsync(argv, ssh_timeout)
    return (attempted, 0)


def assert_artifact_contract(
    merged: list[dict[str, object]], local_home: str, grill_root: str | None = None
) -> None:
    """Guard the path-form contract: merged is in *local* form.

    If the merged store references any local-form ``grill/`` path,
    ``collect_artifact_paths`` must return them. This FAILS (rather than
    vacuously passing) if a coding change collects from the remote-form
    store instead — the silent-empty-transfer regression this item fixes.
    """
    # Count only "include"-class paths (raw grill prefix that resolves under
    # grill and isn't a symlink/self). Escape paths are excluded by design and
    # warned via warn_nonlocal_related_paths — they must not trip this guard.
    has_in_scope = any(
        _grill_classify(str(p), local_home, grill_root)[0] == "include"
        for item in merged
        for p in _related_paths(item)
    )
    if not has_in_scope:
        return
    assert collect_artifact_paths(merged, local_home, grill_root), (
        "artifact contract broken: merged references local-form grill paths "
        f"but collect_artifact_paths returned empty (home={local_home!r})"
    )


def warn_nonlocal_related_paths(
    items: list[dict[str, object]], local_home: str, grill_root: str | None = None
) -> None:
    """Warn once if a merged ``grill/`` path was excluded by the resolve guard.

    Only fires for paths that *look* like grill paths but escaped (``..``/
    symlink) or are the directory itself — never for ordinary project-source
    ``related_files`` entries, which are correctly out of scope.
    """
    grill_prefix = grill_root_for(local_home, grill_root) + "/"
    seen_warnings: set[str] = set()
    for item in items:
        for p in _related_paths(item):
            _, warning = _grill_classify(str(p), local_home, grill_root)
            if warning and warning not in seen_warnings:
                seen_warnings.add(warning)
                _warn(warning)
    if len(seen_warnings) > 1:
        _warn(
            f"{len(seen_warnings)} related_files path(s) under {grill_prefix} "
            "excluded by artifact guard (escape/symlink/self) — not transferred"
        )


def artifact_preview(
    merged: list[dict[str, object]],
    local_home: str,
    remote_home: str,
    host: str,
    *,
    quiet: bool,
    local_root: str | None = None,
    remote_root: str | None = None,
) -> None:
    """Print the would-transfer artifact set (no network I/O)."""
    lroot = grill_root_for(local_home, local_root)
    rroot = grill_root_for(remote_home, remote_root)
    collected = collect_artifact_paths(merged, local_home, lroot)
    if not collected:
        cli_common.qprint(f"artifacts: none (no {lroot}/ related_files)", quiet=quiet)
        return
    rels = sorted({_rel_for_root(p, lroot) for p in collected})
    cli_common.qprint(
        f"artifacts ({len(collected)} file(s) under {lroot}/):",
        quiet=quiet,
    )
    for rel in rels:
        cli_common.qprint(f"  push  {lroot}/./{rel} -> {host}:{rroot}/", quiet=quiet)
        cli_common.qprint(f"  pull  {host}:{rroot}/./{rel} -> {lroot}/", quiet=quiet)


# ── 3-way merge algorithm ──────────────────────────────────────────────────────


def _equal_ignore_updated(a: dict[str, object], b: dict[str, object]) -> bool:
    """Compare two items' full content, excluding the ``updated`` stamp.

    ``updated``'s date-only granularity means two machines making the same
    edit on the same calendar day would otherwise manufacture a false
    conflict purely from the stamp.
    """
    ak = {k: v for k, v in a.items() if k != "updated"}
    bk = {k: v for k, v in b.items() if k != "updated"}
    return ak == bk


def _canonical_updated(a: dict[str, object], b: dict[str, object]) -> object:
    """Return ``max(a['updated'], b['updated'])`` so convergent edits fully converge."""
    return max(cast(str, a.get("updated", "")), cast(str, b.get("updated", "")))


def _pick_winner(
    local_item: dict[str, object], remote_item: dict[str, object]
) -> tuple[dict[str, object], str]:
    """Resolve a divergent-edit conflict: newer ``updated`` wins, same-day tie -> local."""
    lu = cast(str, local_item.get("updated", ""))
    ru = cast(str, remote_item.get("updated", ""))
    if lu >= ru:
        return local_item, "local"
    return remote_item, "remote"


def _make_conflict(
    kind: str, store: str, item_id: str, payload: dict[str, object]
) -> dict[str, object]:
    return {
        "timestamp": datetime.now().isoformat(),  # noqa: DTZ005 (naive stamp, matches dev_status.py's own convention)
        "type": kind,
        "store": store,
        "item_id": item_id,
        "payload": payload,
    }


def merge_item(
    item_id: str,
    base_item: dict[str, object] | None,
    local_item: dict[str, object] | None,
    remote_item: dict[str, object] | None,
    store: str,
) -> tuple[dict[str, object] | None, dict[str, object] | None]:
    """Run the per-item 3-way merge (cases 0-6 of the plan).

    A ``None`` ``base_item`` covers both "true first sync" (case 0) and
    "id added since the last sync" (case 1) — both resolve identically:
    union-merge semantics for this one id.

    Returns:
        ``(merged_item_or_None, conflict_entry_or_None)``. ``merged_item`` is
        ``None`` when the item is dropped (a clean delete).
    """
    if base_item is None:
        if local_item is not None and remote_item is not None:
            if _equal_ignore_updated(local_item, remote_item):
                merged = dict(local_item)
                merged["updated"] = _canonical_updated(local_item, remote_item)
                return merged, None
            winner, side = _pick_winner(local_item, remote_item)
            conflict = _make_conflict(
                "divergent",
                store,
                item_id,
                {"local": local_item, "remote": remote_item, "winner": side},
            )
            return dict(winner), conflict
        if local_item is not None:
            return dict(local_item), None
        if remote_item is not None:
            return dict(remote_item), None
        return None, None

    local_eq_base = local_item is not None and _equal_ignore_updated(
        local_item, base_item
    )
    remote_eq_base = remote_item is not None and _equal_ignore_updated(
        remote_item, base_item
    )

    if local_item is not None and remote_item is not None:
        if local_eq_base and remote_eq_base:
            return dict(local_item), None
        if local_eq_base and not remote_eq_base:
            return dict(remote_item), None
        if remote_eq_base and not local_eq_base:
            return dict(local_item), None
        if _equal_ignore_updated(local_item, remote_item):
            merged = dict(local_item)
            merged["updated"] = _canonical_updated(local_item, remote_item)
            return merged, None
        winner, side = _pick_winner(local_item, remote_item)
        conflict = _make_conflict(
            "divergent",
            store,
            item_id,
            {"local": local_item, "remote": remote_item, "winner": side},
        )
        return dict(winner), conflict

    if local_item is not None and remote_item is None:
        if local_eq_base:
            return None, None
        conflict = _make_conflict(
            "resurrection",
            store,
            item_id,
            {
                "resurrected_item": local_item,
                "resurrected_from": "local",
                "deleted_by": "remote",
            },
        )
        return dict(local_item), conflict

    if remote_item is not None and local_item is None:
        if remote_eq_base:
            return None, None
        conflict = _make_conflict(
            "resurrection",
            store,
            item_id,
            {
                "resurrected_item": remote_item,
                "resurrected_from": "remote",
                "deleted_by": "local",
            },
        )
        return dict(remote_item), conflict

    return None, None


def merge_store(
    base_list: list[dict[str, object]] | None,
    local_list: list[dict[str, object]],
    remote_list: list[dict[str, object]],
    store: str,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Merge one store (items.json or pending_items.json) across all ids.

    Returns:
        ``(merged_list, conflicts)``. Write-need is not decided here — the
        graph-integrity pass (dangling-blocker purge, cycle breaking) can
        still mutate the result afterward, so "does this need writing" is
        computed once, at the end, against the fully-settled merge result.
    """
    base_idx = {i["id"]: i for i in (base_list or [])}
    local_idx = {i["id"]: i for i in local_list}
    remote_idx = {i["id"]: i for i in remote_list}
    all_ids = set(base_idx) | set(local_idx) | set(remote_idx)

    merged: list[dict[str, object]] = []
    conflicts: list[dict[str, object]] = []
    for item_id in sorted(cast(set[str], all_ids)):
        merged_item, conflict = merge_item(
            cast(str, item_id),
            base_idx.get(item_id),
            local_idx.get(item_id),
            remote_idx.get(item_id),
            store,
        )
        if merged_item is not None:
            merged.append(merged_item)
        if conflict is not None:
            conflicts.append(conflict)
    return merged, conflicts


# ── post-merge graph integrity pass ────────────────────────────────────────────


def _find_cycle_path(
    start: str, new_dep: str, index: dict[str, dict[str, object]]
) -> list[str] | None:
    """Best-effort reconstruction of a cycle's path, for the conflict log only.

    ``dev_status.detect_cycle`` is the actual accept/reject decision (reused
    verbatim, see :func:`_break_cycles`); this only recovers a human-readable
    path for the logged entry.
    """

    def dfs(node: str, visited: set[str]) -> list[str] | None:
        if node == start:
            return [node]
        if node in visited:
            return None
        visited.add(node)
        dep = index.get(node)
        if dep:
            for blocker in cast(list[str], dep.get("blocked_by", [])):
                sub = dfs(blocker, visited)
                if sub is not None:
                    return [node, *sub]
        return None

    result = dfs(new_dep, set())
    return [start, *result] if result is not None else None


def _break_cycles(
    merged_items: list[dict[str, object]],
    base_items: list[dict[str, object]] | None,
) -> list[dict[str, object]]:
    """Drop any new edge that would close a cycle in the merged ``blocked_by`` graph.

    Applies edges incrementally against ``base``'s graph (guaranteed
    acyclic), reusing ``dev_status.detect_cycle`` — the exact check
    ``block`` already runs before adding any edge in normal single-machine
    use. Only edges new relative to ``base`` are ever candidates for
    removal; a long-standing inherited dependency is never touched. Mutates
    ``merged_items`` in place.
    """
    # Seed each item's "so far accepted" edge set with base's inherited edges
    # that *survived* the dangling-blocker purge (already run on merged_items
    # before this pass) — not base's raw edges verbatim. A dangling reference
    # the purge just stripped out of `blocked_by` must not be resurrected
    # here just because it was present in base.
    base_idx = {i["id"]: i for i in (base_items or [])}
    working_index: dict[str, dict[str, object]] = {}
    surviving_inherited: dict[str, list[str]] = {}
    for item in merged_items:
        item_id = cast(str, item["id"])
        base_item = base_idx.get(item_id)
        base_bb = cast(list[str], base_item.get("blocked_by", [])) if base_item else []
        final_bb = cast(list[str], item.get("blocked_by", []))
        survived = [b for b in base_bb if b in final_bb]
        surviving_inherited[item_id] = survived
        working_index[item_id] = {"blocked_by": list(survived)}

    conflicts: list[dict[str, object]] = []
    for item in sorted(merged_items, key=lambda i: cast(str, i["id"])):
        item_id = cast(str, item["id"])
        inherited = surviving_inherited[item_id]
        final_bb = cast(list[str], item.get("blocked_by", []))
        new_edges = [b for b in final_bb if b not in inherited]
        accepted = list(inherited)
        for blocker in new_edges:
            if dev_status.detect_cycle(
                item_id, blocker, cast(dev_status.BacklogIndex, working_index)
            ):
                cycle_path = _find_cycle_path(item_id, blocker, working_index)
                conflicts.append(
                    _make_conflict(
                        "cycle",
                        "items",
                        item_id,
                        {
                            "cycle": cycle_path or [item_id, blocker],
                            "dropped_edge": {"item": item_id, "blocker": blocker},
                        },
                    )
                )
            else:
                accepted.append(blocker)
                working_index[item_id]["blocked_by"] = accepted
        item["blocked_by"] = accepted
    return conflicts


def _check_cross_pool_uniqueness(
    merged_items: list[dict[str, object]], merged_pending: list[dict[str, object]]
) -> None:
    """Hard-fail if merging independently coined the same slug in both pools.

    ``dev_status.py`` treats a slug present in both pools as data
    corruption (``resolve_id`` always resolves it to ``pending``,
    permanently orphaning the backlog record) — the merge must never write
    that state, even transiently.
    """
    overlap = {i["id"] for i in merged_items} & {p["id"] for p in merged_pending}
    if overlap:
        raise SyncFatalError(
            f"cross-pool slug collision after merge: {', '.join(sorted(cast(set[str], overlap)))}"
        )


@dataclass
class SyncComputation:
    merged_items: list[dict[str, object]]
    merged_pending: list[dict[str, object]]
    merged_runs: list[dict[str, object]] = field(default_factory=list)
    conflicts: list[dict[str, object]] = field(default_factory=list)
    needs_local_items_write: bool = False
    needs_local_pending_write: bool = False
    needs_local_runs_write: bool = False
    needs_remote_items_write: bool = False
    needs_remote_pending_write: bool = False
    needs_remote_runs_write: bool = False


def merge_runs(
    local_runs: list[dict[str, object]], remote_runs: list[dict[str, object]]
) -> list[dict[str, object]]:
    """Union two run-evidence lists by ``run_id`` — the runs.jsonl merge rule.

    Unlike the JSON stores (per-id field merge with conflict detection), the
    runs sidecar is append-only: a row's ``run_id`` is its identity, so
    merging is just concatenation of lines whose ``run_id`` is absent from
    the earlier list — local order first, then remote-only rows appended in
    remote order. No deletions ever happen, so there is nothing to
    conflict-resolve; size stays bounded in practice (~200 bytes/run).
    Rows that aren't dicts, or lack a non-empty string ``run_id`` (a
    truncated/partial line that somehow got synced), are dropped — they
    carry no citable identity.
    """
    seen: set[str] = set()
    merged: list[dict[str, object]] = []
    for run in [*local_runs, *remote_runs]:
        if not isinstance(run, dict):
            continue
        run_id = run.get("run_id")
        if not isinstance(run_id, str) or not run_id or run_id in seen:
            continue
        seen.add(run_id)
        merged.append(run)
    return merged


def compute_sync(
    base_items: list[dict[str, object]] | None,
    base_pending: list[dict[str, object]] | None,
    local_items: list[dict[str, object]],
    local_pending: list[dict[str, object]],
    remote_items: list[dict[str, object]],
    remote_pending: list[dict[str, object]],
    *,
    local_runs: list[dict[str, object]] | None = None,
    remote_runs: list[dict[str, object]] | None = None,
) -> SyncComputation:
    """Run the full merge: per-store 3-way merge, then graph integrity, then write-need.

    ``remote_items``/``remote_pending`` must already be in this machine's
    canonical ``related_files`` path form (see
    :func:`rewrite_paths_list`) — this function does no path mapping itself.

    Run evidence merges by :func:`merge_runs` (union-by-``run_id``); pass
    ``local_runs``/``remote_runs`` to include it, otherwise the returned
    computation simply carries empty run fields.
    """
    merged_items, item_conflicts = merge_store(
        base_items, local_items, remote_items, "items"
    )
    merged_pending, pending_conflicts = merge_store(
        base_pending, local_pending, remote_pending, "pending_items"
    )

    local_runs = local_runs or []
    remote_runs = remote_runs or []
    merged_runs = merge_runs(local_runs, remote_runs)

    all_prior_ids = (
        {i["id"] for i in (base_items or [])}
        | {i["id"] for i in local_items}
        | {i["id"] for i in remote_items}
        | {i["id"] for i in (base_pending or [])}
        | {i["id"] for i in local_pending}
        | {i["id"] for i in remote_pending}
    )
    merged_ids = {i["id"] for i in merged_items} | {i["id"] for i in merged_pending}
    removed_slugs = cast(set[str], all_prior_ids - merged_ids)
    dev_status._purge_inbound_refs(
        removed_slugs,
        cast(list[dev_status.BacklogItem], merged_items),
        cast(list[dev_status.PendingItem], merged_pending),
    )

    cycle_conflicts = _break_cycles(merged_items, base_items)

    _check_cross_pool_uniqueness(merged_items, merged_pending)

    def by_id(items: list[dict[str, object]]) -> dict[object, dict[str, object]]:
        return {i["id"]: i for i in items}

    return SyncComputation(
        merged_items=merged_items,
        merged_pending=merged_pending,
        merged_runs=merged_runs,
        conflicts=[*item_conflicts, *pending_conflicts, *cycle_conflicts],
        needs_local_items_write=by_id(local_items) != by_id(merged_items),
        needs_local_pending_write=by_id(local_pending) != by_id(merged_pending),
        needs_local_runs_write=merged_runs != local_runs,
        needs_remote_items_write=by_id(remote_items) != by_id(merged_items),
        needs_remote_pending_write=by_id(remote_pending) != by_id(merged_pending),
        needs_remote_runs_write=merged_runs != remote_runs,
    )


# ── local commit (write ordering) ──────────────────────────────────────────────


def local_commit(
    local_schema: dict[str, object],
    result: SyncComputation,
    local_items_raw: list[dict[str, object]],
    local_pending_raw: list[dict[str, object]],
    base_items: list[dict[str, object]] | None,
    base_pending: list[dict[str, object]] | None,
    host: str | None = None,
) -> int | None:
    """Perform the three independently-conditioned writes, in crash-safe order.

    Order: items.json/pending_items.json (if locally changed) -> conflict
    log flush (unconditional, whenever this point is reached at all) ->
    ``_sync-base.json`` (if the merge result differs from the old base on
    either side) -> rev bump (only if a local content write actually
    happened) -> one journal event, if a rev bump happened. See the plan's
    "Write ordering" section for why each condition is independent — the
    modal conflict case (local wins) is exactly the case where local's
    content already matches the merge result, so gating the conflict-log
    flush on "local needs writing" would silently defeat it.

    The journal event is a local discontinuity marker, not journal syncing
    (v1 stays machine-local) — it tells the recap's prose a merge happened
    so it leans on the current bucket summaries instead of assuming a
    continuous local narrative. ``local_lock`` (held by every caller of this
    function) doesn't protect ``journal.jsonl`` against a concurrent
    *local* ``dev_status.py`` append, so this briefly takes
    ``dev_status.backlog_lock()`` itself, same as any other journal writer.
    """
    if result.needs_local_items_write:
        dev_status._backup_before_bulk_delete(dev_status.ITEMS_FILE)
    if result.needs_local_pending_write:
        dev_status._backup_before_bulk_delete(dev_status.PENDING_FILE)

    if result.needs_local_items_write:
        dev_status.save_items(cast(list[dev_status.BacklogItem], result.merged_items))
    if result.needs_local_pending_write:
        dev_status.save_pending(
            cast(list[dev_status.PendingItem], result.merged_pending)
        )
    # Runs union has no rev/backup semantics — an append-only sidecar whose
    # merge can only add rows. Written under a brief backlog_lock (same
    # guard the run/appends use) so a concurrent local run can't interleave.
    if result.needs_local_runs_write:
        with dev_status.backlog_lock():
            dev_status.write_runs_file(
                cast(list[dev_status.RunRecord], result.merged_runs)
            )

    if result.conflicts:
        _append_conflict_log(result.conflicts)

    needs_base_write = (base_items != result.merged_items) or (
        base_pending != result.merged_pending
    )
    if needs_base_write:
        save_sync_base(local_schema, result.merged_items, result.merged_pending)

    if not (result.needs_local_items_write or result.needs_local_pending_write):
        return None

    new_rev = dev_status.bump_rev()
    with dev_status.backlog_lock():
        dev_status.append_journal_event(
            dev_status._journal_entry(
                "sync", "sync", new_rev, summary=f"merged state from {host or 'remote'}"
            )
        )
    return new_rev


# ── transport ───────────────────────────────────────────────────────────────────


def _check_protocol_version(payload: dict[str, object]) -> None:
    if payload.get("protocol_version") != PROTOCOL_VERSION:
        raise SyncFatalError(
            "remote/local script version mismatch (got protocol_version="
            f"{payload.get('protocol_version')!r}, expected {PROTOCOL_VERSION}) — "
            "redeploy dev_status_sync.py"
        )


def _check_schema_versions(
    local_schema: dict[str, object], remote_schema: dict[str, object]
) -> None:
    """Hard-refuse a genuine cross-machine schema disagreement, per store.

    A ``None`` on either side means that store's file doesn't exist yet on
    that machine (e.g. no pending item has ever been created there) — the
    same state ``dev_status.load_pending()`` treats as "no items," not a
    schema violation. That's not a disagreement to guard against; only
    compare when both sides actually have a recorded version.
    """
    for key in ("items", "pending_items"):
        local_v = local_schema.get(key)
        remote_v = remote_schema.get(key)
        if local_v is None or remote_v is None:
            continue
        if local_v != remote_v:
            raise SyncFatalError(
                f"schema_version mismatch for {key}: local={local_v!r} "
                f"remote={remote_v!r} — refusing to merge"
            )


def _extract_framed_json(text: str) -> dict[str, object]:
    m = _FRAME_RE.search(text)
    if not m:
        raise SyncFatalError(
            "no DEVSTATUS_SYNC_JSON markers found in payload — remote/local "
            "script version mismatch, redeploy dev_status_sync.py"
        )
    try:
        return cast(dict[str, object], json.loads(m.group(2)))
    except json.JSONDecodeError as e:
        raise SyncFatalError(f"malformed JSON payload: {e}") from e


def ssh_run(
    host: str,
    remote_script: str,
    remote_args: list[str],
    ssh_timeout: float,
    input_bytes: bytes | None = None,
) -> bytes:
    """Run ``remote_script`` on ``host`` over SSH, bounded against a hung network.

    Interprets OpenSSH's own exit 255 (connection failure) and our own
    protocol's exit 2 (stale rev / lock timeout) as retryable; anything else
    non-zero is fatal.
    """
    cmd = [
        "ssh",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "ServerAliveInterval=5",
        "-o",
        "ServerAliveCountMax=2",
        host,
        "python3",
        remote_script,
        *remote_args,
    ]
    try:
        proc = subprocess.run(
            cmd,
            input=input_bytes,
            capture_output=True,
            timeout=ssh_timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise SyncRetryableError(
            f"ssh to {host} timed out after {ssh_timeout}s: {e}"
        ) from e

    stderr_text = proc.stderr.decode("utf-8", errors="replace")
    if proc.returncode == 255:
        raise SyncRetryableError(
            f"ssh transport failure (exit 255) to {host}: {stderr_text}"
        )
    if proc.returncode == 2:
        raise SyncRetryableError(
            f"remote reported a retryable condition: {stderr_text}"
        )
    if proc.returncode != 0:
        raise SyncFatalError(
            f"remote command failed (exit {proc.returncode}): {stderr_text}"
        )
    return proc.stdout


def ssh_export(host: str, remote_script: str, ssh_timeout: float) -> dict[str, object]:
    stdout = ssh_run(host, remote_script, ["export"], ssh_timeout)
    return _extract_framed_json(stdout.decode("utf-8", errors="replace"))


def ssh_state(host: str, remote_script: str, ssh_timeout: float) -> dict[str, object]:
    """The remote machine's current migration/layout state (see ``state``)."""
    payload = _extract_framed_json(
        ssh_run(host, remote_script, ["state"], ssh_timeout).decode(
            "utf-8", errors="replace"
        )
    )
    _check_protocol_version(payload)
    state = payload.get("machine_state")
    if not isinstance(state, dict):
        raise SyncFatalError(f"{host} returned no machine state")
    return cast(dict[str, object], state)


def ssh_import(
    host: str,
    remote_script: str,
    ssh_timeout: float,
    items: list[dict[str, object]],
    pending: list[dict[str, object]],
    runs: list[dict[str, object]],
    schema: dict[str, object],
    if_rev: int,
    expected_remote_state: dict[str, object] | None = None,
) -> None:
    payload = {
        "protocol_version": PROTOCOL_VERSION,
        "expected_remote_state": expected_remote_state,
        "schema_version": schema,
        "items": items,
        "pending_items": pending,
        "runs": runs,
    }
    nonce = secrets.token_hex(8)
    framed = (
        f"==={_FRAME_MARK}_START:{nonce}===\n"
        f"{json.dumps(payload)}\n"
        f"==={_FRAME_MARK}_END:{nonce}===\n"
    ).encode()
    ssh_run(
        host, remote_script, ["import", "--if-rev", str(if_rev)], ssh_timeout, framed
    )


# ── diff / report output ───────────────────────────────────────────────────────


def _categorize(
    store: str,
    local_pre: list[dict[str, object]],
    remote_pre: list[dict[str, object]],
    merged: list[dict[str, object]],
    conflicts: list[dict[str, object]],
) -> dict[str, list[str]]:
    local_idx = {i["id"]: i for i in local_pre}
    remote_idx = {i["id"]: i for i in remote_pre}
    merged_idx = {i["id"]: i for i in merged}
    conflict_ids = {c["item_id"] for c in conflicts if c["store"] == store}

    cats: dict[str, list[str]] = {
        "added_from_remote": [],
        "added_from_local": [],
        "updated": [],
        "deleted": [],
        "unchanged": [],
    }
    all_ids = set(local_idx) | set(remote_idx) | set(merged_idx)
    for item_id in sorted(cast(set[str], all_ids)):
        if item_id in conflict_ids:
            continue
        in_local, in_remote, in_merged = (
            item_id in local_idx,
            item_id in remote_idx,
            item_id in merged_idx,
        )
        if in_merged and in_local and in_remote:
            if merged_idx[item_id] == local_idx[item_id] == remote_idx[item_id]:
                cats["unchanged"].append(item_id)
            else:
                cats["updated"].append(item_id)
        elif in_merged and in_remote and not in_local:
            cats["added_from_remote"].append(item_id)
        elif in_merged and in_local and not in_remote:
            cats["added_from_local"].append(item_id)
        elif not in_merged and (in_local or in_remote):
            cats["deleted"].append(item_id)
        elif in_merged:
            cats["added_from_remote"].append(item_id)
    return cats


def print_diff(
    result: SyncComputation,
    local_items: list[dict[str, object]],
    local_pending: list[dict[str, object]],
    remote_items: list[dict[str, object]],
    remote_pending: list[dict[str, object]],
    local_rev: int,
    remote_rev: int,
    header: str,
    quiet: bool = False,
) -> None:
    cli_common.qprint(f"=== {header} ===", quiet=quiet)
    for store, local_pre, remote_pre, merged in (
        ("items", local_items, remote_items, result.merged_items),
        ("pending_items", local_pending, remote_pending, result.merged_pending),
    ):
        cats = _categorize(store, local_pre, remote_pre, merged, result.conflicts)
        cli_common.qprint(f"-- {store} --", quiet=quiet)
        cli_common.qprint(
            f"  added-on-remote ({len(cats['added_from_remote'])}): {cats['added_from_remote']}",
            quiet=quiet,
        )
        cli_common.qprint(
            f"  added-on-local  ({len(cats['added_from_local'])}): {cats['added_from_local']}",
            quiet=quiet,
        )
        cli_common.qprint(
            f"  updated         ({len(cats['updated'])}): {cats['updated']}",
            quiet=quiet,
        )
        cli_common.qprint(
            f"  deleted         ({len(cats['deleted'])}): {cats['deleted']}",
            quiet=quiet,
        )
        cli_common.qprint(
            f"  unchanged       ({len(cats['unchanged'])})",
            quiet=quiet,
        )
    if result.conflicts:
        cli_common.qprint(f"-- conflicts ({len(result.conflicts)}) --", quiet=quiet)
        for c in result.conflicts:
            cli_common.qprint(
                f"  [{c['type']}] {c['store']}:{c['item_id']}",
                quiet=quiet,
            )
            cli_common.qprint(
                json.dumps(c["payload"], indent=2, sort_keys=True),
                quiet=quiet,
            )
    cli_common.qprint(f"local rev: {local_rev} · remote rev: {remote_rev}", quiet=quiet)


# ── subcommand handlers ──────────────────────────────────────────────────────────


def cmd_export(args: argparse.Namespace) -> None:
    """``export``: dump this machine's local store+rev as one framed JSON blob."""
    with migration_guard() as state, local_lock(args.lock_timeout):
        items, items_schema = _read_store_file(dev_status.ITEMS_FILE)
        pending, pending_schema = _read_store_file(dev_status.PENDING_FILE)
        rev = dev_status.load_rev()
        runs = dev_status.load_runs()

    payload = {
        "protocol_version": PROTOCOL_VERSION,
        "machine_state": state,
        "rev": rev,
        "schema_version": {"items": items_schema, "pending_items": pending_schema},
        "items": items,
        "pending_items": pending,
        "runs": runs,
    }
    nonce = secrets.token_hex(8)
    print(f"==={_FRAME_MARK}_START:{nonce}===")
    print(json.dumps(payload))
    print(f"==={_FRAME_MARK}_END:{nonce}===")


def cmd_import(args: argparse.Namespace) -> None:
    """``import``: write a merged store from stdin, iff local rev == --if-rev."""
    raw_stdin = sys.stdin.read()
    payload = _extract_framed_json(raw_stdin)
    _check_protocol_version(payload)

    with migration_guard() as state, local_lock(args.lock_timeout):
        expected = payload.get("expected_remote_state")
        if (
            state["migration"] in _UNSAFE_MIGRATION_STATES
            or not isinstance(expected, dict)
            or any(state[k] != expected.get(k) for k in ("layout", "decisions_root"))
        ):
            raise SyncFatalError(
                "refusing import: this machine's migration/layout state changed "
                f"since export (expected {expected}, now {state})"
            )
        _, local_items_schema = _read_store_file(dev_status.ITEMS_FILE)
        _, local_pending_schema = _read_store_file(dev_status.PENDING_FILE)
        local_schema = {
            "items": local_items_schema,
            "pending_items": local_pending_schema,
        }
        _check_schema_versions(
            local_schema, cast(dict[str, object], payload["schema_version"])
        )

        current_rev = dev_status.load_rev()
        if current_rev != args.if_rev:
            raise SyncRetryableError(
                f"stale rev: --if-rev {args.if_rev} given, current is {current_rev}"
            )

        dev_status.save_items(cast(list[dev_status.BacklogItem], payload["items"]))
        dev_status.save_pending(
            cast(list[dev_status.PendingItem], payload["pending_items"])
        )
        # Run evidence: union-by-run_id against whatever was recorded locally
        # since the other machine exported (defensive — the import payload is
        # normally already the merged union). Runs carry no rev, so this
        # write doesn't interact with the --if-rev guard.
        payload_runs = payload.get("runs")
        if isinstance(payload_runs, list):
            existing_runs = dev_status.load_runs()
            merged_runs = merge_runs(existing_runs, payload_runs)
            if merged_runs != existing_runs:
                dev_status.write_runs_file(
                    cast(list[dev_status.RunRecord], merged_runs)
                )
        new_rev = dev_status.bump_rev()

    cli_common.vprint(
        f"[import] wrote rev {new_rev}", verbose=getattr(args, "verbose", False)
    )


def cmd_state(args: argparse.Namespace) -> None:
    """``state``: print this machine's migration/layout state as framed JSON."""
    with migration_guard() as state:
        payload = {"protocol_version": PROTOCOL_VERSION, "machine_state": state}
    nonce = secrets.token_hex(8)
    print(f"==={_FRAME_MARK}_START:{nonce}===")
    print(json.dumps(payload))
    print(f"==={_FRAME_MARK}_END:{nonce}===")


def cmd_status(args: argparse.Namespace) -> None:
    """``status``: report divergence (revs, counts, per-side ids) without merging."""
    local_items_raw, local_items_schema = _read_store_file(dev_status.ITEMS_FILE)
    local_pending_raw, local_pending_schema = _read_store_file(dev_status.PENDING_FILE)
    local_rev = dev_status.load_rev()
    local_schema = {"items": local_items_schema, "pending_items": local_pending_schema}

    remote_payload = ssh_export(args.host, args.remote_script, args.ssh_timeout)
    _check_protocol_version(remote_payload)
    remote_schema = cast(dict[str, object], remote_payload["schema_version"])
    _check_schema_versions(local_schema, remote_schema)

    local_home = args.user_map[args.local_user]
    remote_home = args.user_map[args.remote_user]
    remote_items = rewrite_paths_list(
        cast(list[dict[str, object]], remote_payload["items"]), remote_home, local_home
    )
    remote_pending = rewrite_paths_list(
        cast(list[dict[str, object]], remote_payload["pending_items"]),
        remote_home,
        local_home,
    )

    base_items, base_pending = load_sync_base(local_schema)
    result = compute_sync(
        base_items,
        base_pending,
        local_items_raw,
        local_pending_raw,
        remote_items,
        remote_pending,
    )
    print_diff(
        result,
        local_items_raw,
        local_pending_raw,
        remote_items,
        remote_pending,
        local_rev,
        cast(int, remote_payload["rev"]),
        "status (report only, no writes)",
        quiet=getattr(args, "quiet", False),
    )
    # Preview only — status performs no network writes beyond the export above.
    artifact_preview(
        result.merged_items,
        local_home,
        remote_home,
        args.host,
        quiet=getattr(args, "quiet", False),
    )


def cmd_sync(args: argparse.Namespace) -> None:
    """``sync``: merge against the other machine (staged export/import)."""
    local_home = args.user_map[args.local_user]
    remote_home = args.user_map[args.remote_user]
    rsync_io_timeout = (
        args.rsync_io_timeout
        if getattr(args, "rsync_io_timeout", None) is not None
        else args.ssh_timeout
    )

    with migration_guard() as local_state, local_lock(args.lock_timeout):
        local_items_raw, local_items_schema = _read_store_file(dev_status.ITEMS_FILE)
        local_pending_raw, local_pending_schema = _read_store_file(
            dev_status.PENDING_FILE
        )
        local_rev = dev_status.load_rev()
        local_schema = {
            "items": local_items_schema,
            "pending_items": local_pending_schema,
        }
        base_items, base_pending = load_sync_base(local_schema)

        attempt = 0
        result: SyncComputation | None = None
        remote_payload: dict[str, object] | None = None
        remote_state: dict[str, object] = {}
        local_root = local_state["decisions_root"]
        remote_root = ""
        push_count = 0
        pull_count = 0
        while True:
            attempt += 1
            try:
                remote_payload = ssh_export(
                    args.host, args.remote_script, args.ssh_timeout
                )
                _check_protocol_version(remote_payload)
                refuse_unsafe_states(
                    local_state, remote_payload.get("machine_state"), args.host
                )
                remote_state = cast(dict[str, object], remote_payload["machine_state"])
                remote_root = str(remote_state.get("decisions_root") or "")
                inbound_roots = (remote_root, local_root) if remote_root else None
                outbound_roots = (local_root, remote_root) if remote_root else None
                remote_schema = cast(
                    dict[str, object], remote_payload["schema_version"]
                )
                _check_schema_versions(local_schema, remote_schema)

                remote_items = rewrite_paths_list(
                    cast(list[dict[str, object]], remote_payload["items"]),
                    remote_home,
                    local_home,
                    inbound_roots,
                )
                remote_pending = rewrite_paths_list(
                    cast(list[dict[str, object]], remote_payload["pending_items"]),
                    remote_home,
                    local_home,
                    inbound_roots,
                )

                result = compute_sync(
                    base_items,
                    base_pending,
                    local_items_raw,
                    local_pending_raw,
                    remote_items,
                    remote_pending,
                    local_runs=dev_status.load_runs(),
                    remote_runs=cast(
                        list[dict[str, object]], remote_payload.get("runs", [])
                    ),
                )

                if args.dry_run:
                    cli_common.qprint(
                        f"machine state: this machine {local_state['layout']}/"
                        f"{local_state['migration']}, {args.host} "
                        f"{remote_state.get('layout')}/{remote_state.get('migration')}",
                        quiet=getattr(args, "quiet", False),
                    )
                    print_diff(
                        result,
                        local_items_raw,
                        local_pending_raw,
                        remote_items,
                        remote_pending,
                        local_rev,
                        cast(int, remote_payload["rev"]),
                        "sync --dry-run (no writes)",
                        quiet=getattr(args, "quiet", False),
                    )
                    if not args.no_artifacts:
                        artifact_preview(
                            result.merged_items,
                            local_home,
                            remote_home,
                            args.host,
                            quiet=getattr(args, "quiet", False),
                            local_root=local_root,
                            remote_root=remote_root or None,
                        )
                    return

                # Artifact path-form contract (regression guard) + escape warning.
                assert_artifact_contract(result.merged_items, local_home, local_root)
                warn_nonlocal_related_paths(result.merged_items, local_home, local_root)

                # Artifact transfer is decoupled from the JSON-dirty gate: it runs
                # whenever there are collectable grill paths and --no-artifacts is
                # unset. Push FIRST — if it fails, abort before ssh_import so the
                # remote metadata never references an absent file.
                if not args.no_artifacts and collect_artifact_paths(
                    result.merged_items, local_home, local_root
                ):
                    if shutil.which("rsync") is None or not remote_has_rsync(
                        args.host, args.ssh_timeout
                    ):
                        raise SyncFatalError(
                            "rsync unavailable locally or on remote; install rsync "
                            "or pass --no-artifacts (metadata-only sync, artifacts "
                            "will NOT transfer)"
                        )
                    push_attempted, _ = push_artifacts(
                        args.host,
                        result.merged_items,
                        local_home,
                        remote_home,
                        args.ssh_timeout,
                        rsync_io_timeout,
                        quiet=getattr(args, "quiet", False),
                        dry_run=False,
                        local_root=local_root,
                        remote_root=remote_root or None,
                    )
                    push_count = push_attempted

                if (
                    result.needs_remote_items_write
                    or result.needs_remote_pending_write
                    or result.needs_remote_runs_write
                ):
                    outbound_items = rewrite_paths_list(
                        result.merged_items, local_home, remote_home, outbound_roots
                    )
                    outbound_pending = rewrite_paths_list(
                        result.merged_pending, local_home, remote_home, outbound_roots
                    )
                    ssh_import(
                        args.host,
                        args.remote_script,
                        args.ssh_timeout,
                        outbound_items,
                        outbound_pending,
                        result.merged_runs,
                        remote_schema,
                        cast(int, remote_payload["rev"]),
                        {
                            "layout": remote_state.get("layout"),
                            "decisions_root": remote_state.get("decisions_root"),
                        },
                    )

                # Pull (remote -> local) so remote-created/edited files exist
                # before the local metadata commit.
                if not args.no_artifacts and collect_artifact_paths(
                    result.merged_items, local_home, local_root
                ):
                    pull_attempted, _ = pull_artifacts(
                        args.host,
                        result.merged_items,
                        local_home,
                        remote_home,
                        args.ssh_timeout,
                        rsync_io_timeout,
                        quiet=getattr(args, "quiet", False),
                        dry_run=False,
                        local_root=local_root,
                        remote_root=remote_root or None,
                    )
                    pull_count = pull_attempted

                break
            except SyncRetryableError as e:
                if attempt >= args.max_retries:
                    raise SyncFatalError(
                        f"exhausted {args.max_retries} retries: {e}"
                    ) from e
                cli_common.vprint(
                    f"[sync] retryable condition (attempt {attempt}/{args.max_retries}): {e} "
                    "— retrying from export",
                    verbose=getattr(args, "verbose", False),
                )
                time.sleep(_LOCK_POLL_INTERVAL + random.uniform(0, 0.1))
                continue

        assert result is not None and remote_payload is not None
        # The remote held its migration lock only inside each SSH call, so
        # re-read its state before committing locally: a migration that ran
        # since export (during artifact transfer, or with no import needed)
        # must not be committed over. A migration starting after this read
        # and before the commit below is an accepted residual window.
        final_remote = ssh_state(args.host, args.remote_script, args.ssh_timeout)
        if final_remote.get("migration") in _UNSAFE_MIGRATION_STATES or any(
            final_remote.get(k) != remote_state.get(k)
            for k in ("layout", "decisions_root")
        ):
            raise SyncFatalError(
                f"{args.host}'s migration/layout state changed during the sync "
                f"(was {remote_state}, now {final_remote}) -- not committing locally"
            )
        new_rev = local_commit(
            local_schema,
            result,
            local_items_raw,
            local_pending_raw,
            base_items,
            base_pending,
            args.host,
        )
        print_diff(
            result,
            local_items_raw,
            local_pending_raw,
            result.merged_items,
            result.merged_pending,
            new_rev if new_rev is not None else local_rev,
            cast(int, remote_payload["rev"]),
            "sync complete",
            quiet=getattr(args, "quiet", False),
        )
        if not args.no_artifacts:
            cli_common.qprint(
                f"artifacts: pushed {push_count}, pulled {pull_count}",
                quiet=getattr(args, "quiet", False),
            )
            artifact_preview(
                result.merged_items,
                local_home,
                remote_home,
                args.host,
                quiet=getattr(args, "quiet", False),
                local_root=local_root,
                remote_root=remote_root or None,
            )


# ── CLI ───────────────────────────────────────────────────────────────────────


def _default_local_user() -> str:
    return os.environ.get("USER") or os.environ.get("LOGNAME") or Path.home().name


def _resolve_user_map(raw: str | None) -> dict[str, str]:
    user_map = dict(DEFAULT_USER_MAP)
    if raw:
        user_map.update(json.loads(raw))
    return user_map


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Cross-machine sync for dev_status.py's backlog/pending store."
    )
    # --quiet/-v are defined once, on every leaf subcommand parser only
    # (via this shared `parents=` parser) -- never on `parser` itself. See
    # dev_status.py's build_parser() for the full rationale. Placed here,
    # before this script's own top-level flags, to keep gen_interfaces.py's
    # source-order-derived option list unchanged.
    verbosity_parent = argparse.ArgumentParser(add_help=False)
    cli_common.add_verbosity_args(verbosity_parent)
    parser.add_argument(
        "--host", default=DEFAULT_HOST, help="SSH alias for the remote machine"
    )
    parser.add_argument("--remote-script", default=DEFAULT_REMOTE_SCRIPT)
    parser.add_argument("--local-user", default=_default_local_user())
    parser.add_argument("--remote-user", default=DEFAULT_REMOTE_USER)
    parser.add_argument(
        "--user-map",
        default=None,
        metavar="<json>",
        help="JSON object overriding the default username->home-dir map",
    )
    parser.add_argument("--lock-timeout", type=float, default=10.0, metavar="<seconds>")
    parser.add_argument("--ssh-timeout", type=float, default=20.0, metavar="<seconds>")
    parser.add_argument("--max-retries", type=int, default=3)

    sub = parser.add_subparsers(dest="command", required=True)

    p_sync = sub.add_parser(
        "sync", help="merge against the other machine", parents=[verbosity_parent]
    )
    p_sync.add_argument("--dry-run", action="store_true")
    p_sync.add_argument(
        "--no-artifacts",
        action="store_true",
        help="skip grill/ artifact transfer (metadata-only sync)",
    )
    p_sync.add_argument(
        "--rsync-io-timeout",
        type=float,
        default=None,
        metavar="<seconds>",
        help="rsync I/O timeout (defaults to --ssh-timeout)",
    )
    p_sync.set_defaults(func=cmd_sync)

    p_status = sub.add_parser(
        "status",
        help="report divergence without merging",
        parents=[verbosity_parent],
    )
    p_status.set_defaults(func=cmd_status)

    p_export = sub.add_parser(
        "export",
        help="internal: dump local store+rev as JSON",
        parents=[verbosity_parent],
    )
    p_export.set_defaults(func=cmd_export)

    p_import = sub.add_parser(
        "import",
        help="internal: write a merged store from stdin",
        parents=[verbosity_parent],
    )
    p_import.add_argument("--if-rev", type=int, required=True, metavar="<N>")
    p_import.set_defaults(func=cmd_import)

    p_state = sub.add_parser(
        "state",
        help="internal: print this machine's migration/layout state as JSON",
        parents=[verbosity_parent],
    )
    p_state.set_defaults(func=cmd_state)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.user_map = _resolve_user_map(args.user_map)
    try:
        args.func(args)
    except SyncFatalError as e:
        print(f"[dev_status_sync] fatal: {e}", file=sys.stderr)
        sys.exit(1)
    except SyncRetryableError as e:
        print(f"[dev_status_sync] retryable: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
