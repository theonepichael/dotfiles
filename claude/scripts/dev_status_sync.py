#!/usr/bin/env python3
"""dev_status_sync.py — cross-machine sync for dev_status.py's backlog/pending store.

Always initiated from this machine (desktop-initiated-only): a run performs a
full bidirectional merge in one command — pulls the remote's state, merges it
against a local 3-way base snapshot, pushes the merged result back to the
remote, and saves locally. ``dev_status.py`` itself is not modified; this is a
standalone bolt-on script, stdlib only.

Transport is staged export -> local merge -> import over SSH, reusing
``dev_status.py``'s own optimistic-concurrency (``--if-rev``) pattern rather
than holding a live bidirectional session. See the companion plan document
for the full design rationale (3-way merge algorithm, conflict log, graph
integrity pass, path mapping, locking).

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
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import cast

sys.path.insert(0, str(Path(__file__).parent))
import dev_status

PROTOCOL_VERSION = 1

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


def rewrite_related_files_paths(
    item: dict[str, object], from_home: str, to_home: str
) -> dict[str, object]:
    """Rewrite a leading ``from_home`` prefix on ``related_files.path`` entries.

    Recomputes ``review_content_hash`` (via ``dev_status._content_hash``)
    whenever a rewrite actually changes ``related_files``, since that field
    feeds the hash and a stale hash would break ``approve``/``reject``.
    Narrow prefix substitution only — not a general path-portability system;
    a path already in ``to_home`` form, or with no ``/home/<user>/`` prefix
    at all (``/mnt/c/...``, ``/tmp/...``), passes through untouched.
    """
    rf = item.get("related_files")
    if not rf or not isinstance(rf, list):
        return item
    prefix = from_home.rstrip("/") + "/"
    changed = False
    new_rf: list[object] = []
    for entry in rf:
        path = entry.get("path") if isinstance(entry, dict) else None
        if (
            isinstance(entry, dict)
            and isinstance(path, str)
            and path.startswith(prefix)
        ):
            new_entry = dict(entry)
            new_entry["path"] = to_home.rstrip("/") + path[len(from_home.rstrip("/")) :]
            new_rf.append(new_entry)
            changed = True
        else:
            new_rf.append(entry)
    if not changed:
        return item
    new_item = dict(item)
    new_item["related_files"] = new_rf
    if "review_content_hash" in new_item:
        new_item["review_content_hash"] = dev_status._content_hash(
            cast(dev_status.BacklogItem, new_item)
        )
    return new_item


def rewrite_paths_list(
    items: list[dict[str, object]], from_home: str, to_home: str
) -> list[dict[str, object]]:
    """Apply :func:`rewrite_related_files_paths` across a whole store."""
    return [rewrite_related_files_paths(it, from_home, to_home) for it in items]


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
    conflicts: list[dict[str, object]] = field(default_factory=list)
    needs_local_items_write: bool = False
    needs_local_pending_write: bool = False
    needs_remote_items_write: bool = False
    needs_remote_pending_write: bool = False


def compute_sync(
    base_items: list[dict[str, object]] | None,
    base_pending: list[dict[str, object]] | None,
    local_items: list[dict[str, object]],
    local_pending: list[dict[str, object]],
    remote_items: list[dict[str, object]],
    remote_pending: list[dict[str, object]],
) -> SyncComputation:
    """Run the full merge: per-store 3-way merge, then graph integrity, then write-need.

    ``remote_items``/``remote_pending`` must already be in this machine's
    canonical ``related_files`` path form (see
    :func:`rewrite_paths_list`) — this function does no path mapping itself.
    """
    merged_items, item_conflicts = merge_store(
        base_items, local_items, remote_items, "items"
    )
    merged_pending, pending_conflicts = merge_store(
        base_pending, local_pending, remote_pending, "pending_items"
    )

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
        conflicts=[*item_conflicts, *pending_conflicts, *cycle_conflicts],
        needs_local_items_write=by_id(local_items) != by_id(merged_items),
        needs_local_pending_write=by_id(local_pending) != by_id(merged_pending),
        needs_remote_items_write=by_id(remote_items) != by_id(merged_items),
        needs_remote_pending_write=by_id(remote_pending) != by_id(merged_pending),
    )


# ── local commit (write ordering) ──────────────────────────────────────────────


def local_commit(
    local_schema: dict[str, object],
    result: SyncComputation,
    local_items_raw: list[dict[str, object]],
    local_pending_raw: list[dict[str, object]],
    base_items: list[dict[str, object]] | None,
    base_pending: list[dict[str, object]] | None,
) -> int | None:
    """Perform the three independently-conditioned writes, in crash-safe order.

    Order: items.json/pending_items.json (if locally changed) -> conflict
    log flush (unconditional, whenever this point is reached at all) ->
    ``_sync-base.json`` (if the merge result differs from the old base on
    either side) -> rev bump (only if a local content write actually
    happened). See the plan's "Write ordering" section for why each
    condition is independent — the modal conflict case (local wins) is
    exactly the case where local's content already matches the merge
    result, so gating the conflict-log flush on "local needs writing" would
    silently defeat it.
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

    if result.conflicts:
        _append_conflict_log(result.conflicts)

    needs_base_write = (base_items != result.merged_items) or (
        base_pending != result.merged_pending
    )
    if needs_base_write:
        save_sync_base(local_schema, result.merged_items, result.merged_pending)

    if result.needs_local_items_write or result.needs_local_pending_write:
        return dev_status.bump_rev()
    return None


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
    for key in ("items", "pending_items"):
        if local_schema.get(key) != remote_schema.get(key):
            raise SyncFatalError(
                f"schema_version mismatch for {key}: local={local_schema.get(key)!r} "
                f"remote={remote_schema.get(key)!r} — refusing to merge"
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


def ssh_import(
    host: str,
    remote_script: str,
    ssh_timeout: float,
    items: list[dict[str, object]],
    pending: list[dict[str, object]],
    schema: dict[str, object],
    if_rev: int,
) -> None:
    payload = {
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": schema,
        "items": items,
        "pending_items": pending,
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
) -> None:
    print(f"=== {header} ===")
    for store, local_pre, remote_pre, merged in (
        ("items", local_items, remote_items, result.merged_items),
        ("pending_items", local_pending, remote_pending, result.merged_pending),
    ):
        cats = _categorize(store, local_pre, remote_pre, merged, result.conflicts)
        print(f"-- {store} --")
        print(
            f"  added-on-remote ({len(cats['added_from_remote'])}): {cats['added_from_remote']}"
        )
        print(
            f"  added-on-local  ({len(cats['added_from_local'])}): {cats['added_from_local']}"
        )
        print(f"  updated         ({len(cats['updated'])}): {cats['updated']}")
        print(f"  deleted         ({len(cats['deleted'])}): {cats['deleted']}")
        print(f"  unchanged       ({len(cats['unchanged'])})")
    if result.conflicts:
        print(f"-- conflicts ({len(result.conflicts)}) --")
        for c in result.conflicts:
            print(f"  [{c['type']}] {c['store']}:{c['item_id']}")
            print(json.dumps(c["payload"], indent=2, sort_keys=True))
    print(f"local rev: {local_rev} · remote rev: {remote_rev}")


# ── subcommand handlers ──────────────────────────────────────────────────────────


def cmd_export(args: argparse.Namespace) -> None:
    """``export``: dump this machine's local store+rev as one framed JSON blob."""
    with local_lock(args.lock_timeout):
        items, items_schema = _read_store_file(dev_status.ITEMS_FILE)
        pending, pending_schema = _read_store_file(dev_status.PENDING_FILE)
        rev = dev_status.load_rev()

    payload = {
        "protocol_version": PROTOCOL_VERSION,
        "rev": rev,
        "schema_version": {"items": items_schema, "pending_items": pending_schema},
        "items": items,
        "pending_items": pending,
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

    with local_lock(args.lock_timeout):
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
        new_rev = dev_status.bump_rev()

    print(f"[import] wrote rev {new_rev}", file=sys.stderr)


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
    )


def cmd_sync(args: argparse.Namespace) -> None:
    """``sync``: merge against the other machine (staged export/import)."""
    local_home = args.user_map[args.local_user]
    remote_home = args.user_map[args.remote_user]

    with local_lock(args.lock_timeout):
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
        while True:
            attempt += 1
            try:
                remote_payload = ssh_export(
                    args.host, args.remote_script, args.ssh_timeout
                )
                _check_protocol_version(remote_payload)
                remote_schema = cast(
                    dict[str, object], remote_payload["schema_version"]
                )
                _check_schema_versions(local_schema, remote_schema)

                remote_items = rewrite_paths_list(
                    cast(list[dict[str, object]], remote_payload["items"]),
                    remote_home,
                    local_home,
                )
                remote_pending = rewrite_paths_list(
                    cast(list[dict[str, object]], remote_payload["pending_items"]),
                    remote_home,
                    local_home,
                )

                result = compute_sync(
                    base_items,
                    base_pending,
                    local_items_raw,
                    local_pending_raw,
                    remote_items,
                    remote_pending,
                )

                if args.dry_run:
                    print_diff(
                        result,
                        local_items_raw,
                        local_pending_raw,
                        remote_items,
                        remote_pending,
                        local_rev,
                        cast(int, remote_payload["rev"]),
                        "sync --dry-run (no writes)",
                    )
                    return

                if result.needs_remote_items_write or result.needs_remote_pending_write:
                    outbound_items = rewrite_paths_list(
                        result.merged_items, local_home, remote_home
                    )
                    outbound_pending = rewrite_paths_list(
                        result.merged_pending, local_home, remote_home
                    )
                    ssh_import(
                        args.host,
                        args.remote_script,
                        args.ssh_timeout,
                        outbound_items,
                        outbound_pending,
                        remote_schema,
                        cast(int, remote_payload["rev"]),
                    )
                break
            except SyncRetryableError as e:
                if attempt >= args.max_retries:
                    raise SyncFatalError(
                        f"exhausted {args.max_retries} retries: {e}"
                    ) from e
                print(
                    f"[sync] retryable condition (attempt {attempt}/{args.max_retries}): {e} "
                    "— retrying from export",
                    file=sys.stderr,
                )
                time.sleep(_LOCK_POLL_INTERVAL + random.uniform(0, 0.1))
                continue

        assert result is not None and remote_payload is not None
        new_rev = local_commit(
            local_schema,
            result,
            local_items_raw,
            local_pending_raw,
            base_items,
            base_pending,
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

    p_sync = sub.add_parser("sync", help="merge against the other machine")
    p_sync.add_argument("--dry-run", action="store_true")
    p_sync.set_defaults(func=cmd_sync)

    p_status = sub.add_parser("status", help="report divergence without merging")
    p_status.set_defaults(func=cmd_status)

    p_export = sub.add_parser("export", help="internal: dump local store+rev as JSON")
    p_export.set_defaults(func=cmd_export)

    p_import = sub.add_parser(
        "import", help="internal: write a merged store from stdin"
    )
    p_import.add_argument("--if-rev", type=int, required=True, metavar="<N>")
    p_import.set_defaults(func=cmd_import)

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
